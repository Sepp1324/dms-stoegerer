"""Celery-Tasks der Verarbeitungs-Pipeline (asynchron, außerhalb des Requests)."""
import logging
import time
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model

from . import pipeline, storage
from .models import DocumentVersion
from .owner import log_ingest_owner_audit, resolve_default_owner

logger = logging.getLogger(__name__)


@shared_task
def process_document_version(version_id: int) -> dict:
    """Verarbeitet eine neu angelegte Version bis ``READY``.

    Die fachliche State Machine läuft synchron in ``pipeline.process_version``;
    anschließend werden KI-Metadatenvorschläge asynchron und unverbindlich
    angestoßen.
    """
    version = DocumentVersion.objects.select_related("document").get(pk=version_id)
    result = pipeline.process_version(version)

    # KI-Vorschläge nach dem OCR (eigener Task, damit OCR nicht daran hängt).
    from ai.tasks import suggest_document_metadata

    suggest_document_metadata.delay(version.document_id)
    return result


@shared_task
def retry_document_version(version_id: int, actor_id: int | None = None) -> dict:
    """Verarbeitet eine FAILED-Version asynchron ab dem fehlgeschlagenen Schritt neu.

    Spiegelt ``process_document_version`` für den Retry-Pfad (STOAA-248): der
    dokument-scoped Retry-Endpoint stößt diesen Task per ``.delay()`` an, damit
    die – potentiell lange – Neuverarbeitung nicht im Request hängt.
    ``pipeline.retry_version`` zählt den Versuch hoch und läuft ab dem
    fehlgeschlagenen Schritt weiter; anschließend werden – wie beim Erstlauf –
    die KI-Metadatenvorschläge unverbindlich neu angestoßen.
    """
    version = DocumentVersion.objects.select_related("document").get(pk=version_id)

    actor = None
    if actor_id is not None:
        from django.contrib.auth import get_user_model

        actor = get_user_model().objects.filter(pk=actor_id).first()

    result = pipeline.retry_version(version, actor=actor)

    # KI-Vorschläge nach dem OCR neu anstoßen (eigener Task, s. process_document_version).
    from ai.tasks import suggest_document_metadata

    suggest_document_metadata.delay(version.document_id)
    return result


@shared_task
def scan_consume_folder() -> dict:
    """Nimmt alle reifen Dateien aus dem Consume-Ordner auf und stößt die Pipeline an.

    Verarbeitete Dateien werden nach ``consume/_processed/`` verschoben, damit
    sie nicht doppelt aufgenommen werden. (Beat-Zeitplan folgt später.)

    NFS-/NAS-Reife: Eine Datei wird erst verarbeitet, wenn seit ihrer letzten
    Änderung mindestens ``settings.CONSUME_MIN_AGE`` Sekunden vergangen sind.
    Das verhindert Teil-Reads von Dateien, die noch langsam über NFS
    geschrieben werden. Zu junge Dateien werden schlicht übersprungen (kein
    Fehler, kein Verschieben) – der nächste Scan holt sie.

    Robustheit: Ein Fehler bei einer einzelnen Datei bricht den Scan der
    übrigen nicht ab und verschluckt die Datei nicht – sie wandert nach
    ``consume/_failed/`` und wird protokolliert.

    Pro-User-Attribution (``settings.CONSUME_PER_USER``): Ist das Flag aktiv,
    liegen die Scans in pro-User-Unterordnern (``CONSUME_DIR/<username>/``). Der
    Ordnername wird auf einen Django-User aufgelöst; alle darin reifen Dateien
    werden diesem als ``owner`` zugeordnet (``_processed/``/``_failed/`` liegen
    pro User-Ordner). Ordner ohne passenden User werden komplett übersprungen
    und protokolliert – niemals owner-los aufgenommen. Default (Flag off) ist
    das unveränderte Flat-Verhalten.
    """
    consume = storage.CONSUME_DIR
    if not consume.exists():
        return {"found": 0, "ingested": [], "skipped": 0, "failed": 0}

    min_age = float(getattr(settings, "CONSUME_MIN_AGE", 15))
    now = time.time()

    if getattr(settings, "CONSUME_PER_USER", False):
        return _scan_per_user(consume, min_age, now)

    # Flat-Modus (Default): Dateien liegen direkt im Consume-Ordner. Ohne
    # pro-User-Ordner greift optional ``CONSUME_DEFAULT_OWNER`` (STOAA-295),
    # damit eingespeiste Dokumente nicht eigentümerlos (und für Nicht-Admins
    # unsichtbar) bleiben. Ist er leer/unbekannt, bleibt owner=None ein
    # bewusster, admin-sichtbarer Triage-Zustand.
    default_owner = resolve_default_owner(getattr(settings, "CONSUME_DEFAULT_OWNER", ""))
    result = _ingest_consume_dir(
        consume, default_owner, min_age, now, fallback_used=default_owner is not None
    )
    return {"found": len(result["ingested"]), **result}


def _scan_per_user(consume: Path, min_age: float, now: float) -> dict:
    """Pro-User-Modus: iteriert die Top-Level-Unterordner von ``consume``.

    Ordnername = Username. Nur Ordner mit passendem Django-User werden
    verarbeitet (case-insensitiv); alle Dateien darin erhalten diesen als
    ``owner``. Ordner mit führendem ``_``/``.`` (z. B. ``_processed`` auf
    Consume-Ebene) und unbekannte Benutzer werden übersprungen.
    """
    user_model = get_user_model()

    ingested = []
    skipped = 0
    failed = 0
    for entry in sorted(consume.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue

        user = user_model.objects.filter(username__iexact=entry.name).first()
        if user is None:
            # Keine stille Fehl-Attribution: unbekannter Ordner wird komplett
            # übersprungen (nicht als owner=None aufgenommen) und protokolliert.
            logger.warning(
                "scan_consume_folder: Unbekannter Benutzer-Ordner %r übersprungen "
                "– kein passender Django-User, keine owner-lose Aufnahme.",
                entry.name,
            )
            continue

        result = _ingest_consume_dir(entry, user, min_age, now)
        ingested.extend(result["ingested"])
        skipped += result["skipped"]
        failed += result["failed"]

    return {
        "found": len(ingested),
        "ingested": ingested,
        "skipped": skipped,
        "failed": failed,
    }


def _ingest_consume_dir(
    base: Path, owner, min_age: float, now: float, *, fallback_used: bool = False
) -> dict:
    """Nimmt alle reifen Dateien direkt in ``base`` auf (ohne Rekursion).

    ``_processed/`` und ``_failed/`` liegen relativ zu ``base``. Wird sowohl im
    Flat-Modus (``base=CONSUME_DIR``, ``owner`` = ``CONSUME_DEFAULT_OWNER`` oder
    None) als auch pro User-Ordner verwendet (``owner`` = aufgelöster
    Django-User). Gibt die aufgenommenen Dokumente sowie Zähler zurück.
    Robustheit unverändert: Reife-Check pro Datei, ``_processed/``-Idempotenz,
    pro-Datei ``try/except`` → ``_failed/``.

    ``fallback_used`` markiert, dass ``owner`` aus ``CONSUME_DEFAULT_OWNER``
    stammt (Flat-Fallback) – dann wird pro Dokument ``owner_fallback``
    protokolliert; bei ``owner=None`` ``triage_ingest``. Der Per-User-Pfad ruft
    ohne ``fallback_used`` auf und hat stets einen echten Owner → kein
    Zusatz-Audit (Verhalten unverändert, STOAA-269).
    """
    processed_dir = base / "_processed"
    failed_dir = base / "_failed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    ingested = []
    skipped = 0
    failed = 0
    for entry in sorted(base.iterdir()):
        if entry.is_dir() or entry.name.startswith("."):
            continue

        # Reife-Check: zu junge (noch nicht fertig geschriebene) Dateien
        # überspringen. ``stat`` kann fehlschlagen, wenn die Datei zwischen
        # ``iterdir`` und hier verschwindet – dann ebenfalls überspringen.
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            skipped += 1
            continue
        if age < min_age:
            skipped += 1
            continue

        try:
            title = entry.stem
            # In den originals-Bereich kopieren, Original aus dem Eingang entfernen.
            storage.ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
            target = _unique(storage.ORIGINALS_DIR / entry.name)
            target.write_bytes(entry.read_bytes())

            document, version = pipeline.create_document_from_file(
                str(target), title=title, size=target.stat().st_size, owner=owner,
                ingest_source="consume",
            )
            # Owner-Herkunft explizit machen (STOAA-295): Flat-Fallback ->
            # ``owner_fallback``, ohne Owner -> ``triage_ingest``. Per-User-Pfad
            # (echter Owner, kein Fallback) erzeugt hier keinen Zusatz-Eintrag.
            log_ingest_owner_audit(
                document,
                owner=owner,
                fallback_used=fallback_used,
                source="consume",
                reason="consume_flat_ohne_owner",
            )
            process_document_version.delay(version.id)
            entry.rename(processed_dir / entry.name)
            ingested.append({"document_id": document.id, "title": title})
        except Exception:
            # Eine fehlerhafte Datei darf weder den Scan abbrechen noch
            # verschluckt werden: nach ``_failed/`` verschieben + loggen.
            failed += 1
            logger.exception(
                "scan_consume_folder: Verarbeitung fehlgeschlagen für %s", entry
            )
            try:
                failed_dir.mkdir(parents=True, exist_ok=True)
                entry.rename(_unique(failed_dir / entry.name))
            except OSError:
                logger.exception(
                    "scan_consume_folder: Verschieben nach _failed/ fehlgeschlagen für %s",
                    entry,
                )

    return {"ingested": ingested, "skipped": skipped, "failed": failed}


@shared_task
def fetch_all_mail_accounts() -> dict:
    """Beat-Task: stößt für jedes aktive IMAP-Konto einen Abruf an (fan-out)."""
    from .models import MailAccount

    ids = list(MailAccount.objects.filter(enabled=True).values_list("id", flat=True))
    for account_id in ids:
        fetch_mail_account.delay(account_id)
    return {"dispatched": len(ids)}


@shared_task
def fetch_mail_account(account_id: int) -> dict:
    """Ruft ein einzelnes IMAP-Konto ab und speist Anhänge in die Pipeline.

    Fehler einzelner Mails brechen den Abruf nicht ab (siehe ``mail.fetch_account``).

    Ein Advisory-Lock pro Konto verhindert, dass sich überlappende Beat-Läufe
    (bzw. mehrere Worker) denselben Postfachabruf doppelt starten.
    """
    from . import mail
    from .models import MailAccount

    with mail.account_fetch_lock(account_id) as acquired:
        if not acquired:
            # Ein Abruf für dieses Konto läuft bereits – diesen Lauf überspringen.
            return {"status": "locked", "account_id": account_id}
        try:
            account = MailAccount.objects.get(pk=account_id)
        except MailAccount.DoesNotExist:
            return {"status": "missing", "account_id": account_id}
        if not account.enabled:
            return {"status": "disabled", "account_id": account_id}
        return mail.fetch_account(account)


@shared_task
def bulk_classify_documents(document_ids, actor_id=None) -> dict:
    """Wendet die Klassifizierungsregeln asynchron auf viele Dokumente an.

    Für große Batches (>10 Dokumente) aus ``DocumentViewSet.bulk_classify``. Die
    ``document_ids`` sind bereits owner-gescopet (der View filtert vor dem
    Dispatch über ``get_queryset``), daher lädt der Task sie ohne weitere
    Rechteprüfung. Zählt ``updated``/``unchanged`` und sammelt Teilfehler in
    ``errors`` (gemeinsame Kernlogik ``classification.classify_documents``).
    """
    from . import classification
    from .models import AuditLogEntry, Document

    documents = list(Document.objects.filter(id__in=document_ids))
    result = classification.classify_documents(documents)

    if documents:
        AuditLogEntry.objects.create(
            actor_id=actor_id,
            action="bulk_classify",
            object_type="Document",
            # object_id ist CharField(64) – bei großen Batches würde eine
            # ID-Liste überlaufen; die vollständigen IDs stehen in ``detail``.
            object_id=f"{len(documents)} Dokumente",
            detail={
                "mode": "async",
                "ids": sorted(d.id for d in documents),
                "updated": result["updated"],
                "unchanged": result["unchanged"],
                "errors": result["errors"],
            },
        )
    return result


def _unique(path: Path) -> Path:
    counter = 1
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        counter += 1
    return candidate
