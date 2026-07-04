"""Celery-Tasks der Verarbeitungs-Pipeline (asynchron, außerhalb des Requests)."""
import logging
import time
from pathlib import Path

from celery import shared_task
from django.conf import settings

from . import pipeline, storage
from .models import DocumentVersion

logger = logging.getLogger(__name__)


@shared_task
def process_document_version(version_id: int) -> dict:
    """Verarbeitet eine neu angelegte Version: Hash-Kette + OCR + Ablage + Audit.

    Stößt anschließend die KI-Metadatenvorschläge an (asynchron, unverbindlich).
    """
    version = DocumentVersion.objects.select_related("document").get(pk=version_id)
    result = pipeline.process_version(version)

    # Regelbasierte Klassifizierung (deterministisch, direkt anwendend) vor der KI.
    from . import classification

    classification.apply_rules(version.document)

    # KI-Vorschläge nach dem OCR (eigener Task, damit OCR nicht daran hängt).
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
    """
    consume = storage.CONSUME_DIR
    if not consume.exists():
        return {"found": 0, "ingested": [], "skipped": 0, "failed": 0}

    processed_dir = consume / "_processed"
    failed_dir = consume / "_failed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    min_age = float(getattr(settings, "CONSUME_MIN_AGE", 15))
    now = time.time()

    ingested = []
    skipped = 0
    failed = 0
    for entry in sorted(consume.iterdir()):
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
                str(target), title=title, size=target.stat().st_size
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

    return {
        "found": len(ingested),
        "ingested": ingested,
        "skipped": skipped,
        "failed": failed,
    }


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


def _unique(path: Path) -> Path:
    counter = 1
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        counter += 1
    return candidate
