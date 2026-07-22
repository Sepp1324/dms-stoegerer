"""Celery-Tasks der Verarbeitungs-Pipeline (asynchron, außerhalb des Requests)."""
import hashlib
import logging
import os
import shutil
import stat
import time
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from . import pipeline, storage
from .models import DocumentVersion
from .owner import log_ingest_owner_audit, resolve_default_owner

logger = logging.getLogger(__name__)


def _consume_max_bytes() -> int:
    return int(getattr(settings, "CONSUME_MAX_FILE_MB", 200)) * 1024 * 1024


def _read_regular_nofollow(path: Path, max_bytes: int) -> bytes:
    """Liest eine reguläre Datei symlink-sicher (Schutz gegen Symlink-Angriffe).

    ``O_NOFOLLOW`` bricht ab, wenn ``path`` ein Symlink ist (TOCTOU-sicher: die
    Prüfung geschieht beim Öffnen). Nach dem Öffnen wird per ``fstat`` erneut auf
    „reguläre Datei" und Größe geprüft. Verhindert, dass ein schreibberechtigter
    NFS-Nutzer via Link auf z. B. ``/proc/self/environ`` Worker-Secrets als
    Dokument importiert.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise OSError("Keine reguläre Datei.")
        if st.st_size > max_bytes:
            raise OSError(f"Datei zu groß ({st.st_size} Bytes > {max_bytes}).")
        # ``max_bytes + 1`` lesen und beim ZUSÄTZLICHEN Byte ABBRECHEN – NICHT
        # still auf ``max_bytes`` kürzen. Sonst würde eine während des Lesens noch
        # wachsende Datei (langsamer NFS-Scan) abgeschnitten gespeichert und als
        # „verarbeitet" weggeräumt (Datenverlust). Zu groß gewordene Dateien holt
        # der nächste Scan, wenn sie stabil sind.
        data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise OSError(
                f"Datei überschreitet das Limit ({max_bytes} Bytes) oder wächst noch."
            )
        # Nach dem Lesen erneut prüfen: Größe/mtime unverändert (Datei war stabil)?
        st_after = os.fstat(fh.fileno())
        if st_after.st_size != st.st_size or st_after.st_mtime_ns != st.st_mtime_ns:
            raise OSError("Datei wurde während des Lesens verändert – erneuter Versuch später.")
        return data


def _ensure_flashcard_entries(version, *, text, max_q):
    """Generiert die MC-Karten der Version EINMALIG und persistiert sie als
    FlashcardSyncEntry-Zeilen. Gibt ``(created: bool, reason: str|None)`` zurück.

    Läuft unter ``select_for_update`` auf der Version, sodass doppelte Trigger
    serialisiert werden und nur EINER generiert (die LLM-Generierung ist nicht-
    deterministisch – erneutes Generieren ergäbe andere Karten). Schreibt NICHT
    auf die Version selbst (die kann WORM/immutable sein), nur eigene Zeilen.
    """
    from ai.services import generate_flashcards

    from .models import FlashcardSyncEntry

    with transaction.atomic():
        # Lock auf die Versionszeile (nur als Serialisierungspunkt; die Version
        # wird NICHT verändert -> kein Konflikt mit dem WORM-save()-Guard).
        DocumentVersion.objects.select_for_update().get(pk=version.pk)
        if FlashcardSyncEntry.objects.filter(version_id=version.pk).exists():
            return True, None  # bereits generiert -> unverändert weiterverwenden

        result = generate_flashcards(text, max_questions=max_q)
        questions = result.get("questions") or []
        if result.get("source") != "ai" or not questions:
            # Nichts persistieren -> ein späterer Lauf generiert erneut.
            return False, result.get("source", "unavailable")

        FlashcardSyncEntry.objects.bulk_create(
            [
                FlashcardSyncEntry(
                    version_id=version.pk,
                    ordinal=i,
                    idempotency_key=f"dms-v{version.pk}-c{i}",
                    payload={"frage": q["frage"], "aussagen": q["aussagen"], "kap": q["kap"]},
                )
                for i, q in enumerate(questions)
            ]
        )
        return True, None


def _claim_flashcard_entries(version_id, *, stale_after):
    """Claimt offene (``pending``) und verwaiste (``in_progress`` älter als
    ``stale_after``) Karten atomar per CAS-Update auf ``in_progress`` und gibt die
    selbst gewonnenen Einträge zurück.

    Zwei parallele Tasks können so nie dieselbe Karte senden: nur der Task, dessen
    ``update`` die Zeile trifft (Rowcount 1), besitzt sie. Verwaiste in_progress
    (Worker-Crash mitten im Push) werden nach Ablauf reklamiert; ein evtl. bereits
    erfolgter POST wird von psychosr über den stabilen ``ext_id`` dedupliziert.
    """
    from .models import FlashcardSyncEntry

    now = timezone.now()
    cutoff = now - stale_after
    claimable = Q(state=FlashcardSyncEntry.State.PENDING) | Q(
        state=FlashcardSyncEntry.State.IN_PROGRESS, claimed_at__lt=cutoff
    )
    candidate_pks = list(
        FlashcardSyncEntry.objects.filter(version_id=version_id)
        .filter(claimable)
        .values_list("pk", flat=True)
    )
    won: list[int] = []
    for pk in candidate_pks:
        updated = (
            FlashcardSyncEntry.objects.filter(pk=pk)
            .filter(claimable)  # CAS: nur wenn immer noch claimbar
            .update(
                state=FlashcardSyncEntry.State.IN_PROGRESS,
                claimed_at=now,
                attempts=models.F("attempts") + 1,
            )
        )
        if updated:
            won.append(pk)
    return list(FlashcardSyncEntry.objects.filter(pk__in=won).order_by("ordinal"))


def _release_claims(entries):
    """Gibt Karten, die geclaimt aber NICHT gesendet wurden (kein POST erfolgt),
    sofort wieder frei (``in_progress`` -> ``pending``). Nur für Karten aufrufen,
    bei denen sicher KEIN POST rausging (sonst droht eine Dublette)."""
    from .models import FlashcardSyncEntry

    for entry in entries:
        FlashcardSyncEntry.objects.filter(
            pk=entry.pk, state=FlashcardSyncEntry.State.IN_PROGRESS
        ).update(state=FlashcardSyncEntry.State.PENDING)


def _set_synced_marker(document, name, *, present):
    """Marker-Tag NUR als Anzeige – spiegelt den Zustand der AKTUELLEN Version.
    Gesetzt, wenn alle Karten gepusht sind; sonst entfernt (neue/offene Version).
    """
    from .models import Tag

    if present:
        marker = Tag.objects.filter(name=name).first() or Tag.objects.create(
            name=name, color="#6366F1"
        )
        document.tags.add(marker)
    else:
        document.tags.remove(*document.tags.filter(name=name))


def _sync_document_flashcards(document_id: int) -> dict:
    """Kern der psychosr-Synchronisation (ohne Celery-Retry-Steuerung).

    Maßgeblich ist der Zustand der **aktuellen Version** (FlashcardSyncEntry),
    NICHT ein dokumentweiter Tag: eine neue Version wird eigenständig generiert
    und gepusht, auch wenn eine frühere Version bereits „synced" war.
    """
    from . import psychosr_client
    from .models import Document, FlashcardSyncEntry

    if not psychosr_client.is_configured():
        return {"status": "disabled", "document_id": document_id}

    try:
        document = Document.objects.select_related("current_version").get(pk=document_id)
    except Document.DoesNotExist:
        return {"status": "missing", "document_id": document_id}

    version = document.current_version
    text = (version.ocr_text if version else "") or ""
    if not version or not text.strip():
        return {"status": "no_text", "document_id": document_id}

    synced_name = getattr(settings, "PSYCHOSR_SYNCED_TAG", "psychosr-synced")
    max_q = getattr(settings, "PSYCHOSR_MAX_QUESTIONS", 8)

    created, reason = _ensure_flashcard_entries(version, text=text, max_q=max_q)
    if not created:
        return {"status": reason, "document_id": document_id, "generated": 0}

    stale = timedelta(minutes=getattr(settings, "PSYCHOSR_CLAIM_STALE_MINUTES", 15))
    max_attempts = int(getattr(settings, "PSYCHOSR_MAX_CARD_ATTEMPTS", 10))
    claimed = _claim_flashcard_entries(version.pk, stale_after=stale)

    pushed = 0
    failed = 0
    title = document.title or f"Dokument {document_id}"
    for idx, entry in enumerate(claimed):
        try:
            psychosr_client.push_flashcard(
                entry.payload, source_title=title, idempotency_key=entry.idempotency_key
            )
        except SoftTimeLimitExceeded:
            # Task bricht ab. Die AKTUELLE Karte ist mehrdeutig (evtl. schon
            # gepostet) -> in_progress lassen (stale-Reclaim/Watchdog übernimmt).
            # Alle NOCH NICHT versuchten Karten sicher wieder freigeben, sonst
            # blieben sie bis zum stale-Timeout blockiert.
            _release_claims(claimed[idx + 1 :])
            raise
        except Exception as exc:  # noqa: BLE001 – einzelne Karte scheitert, Rest weiter
            failed += 1
            logger.warning(
                "psychosr push (v%s c%s) fehlgeschlagen: %s", version.pk, entry.ordinal, exc
            )
            # Zu oft gescheitert -> endgültig FAILED (kein Endlos-Retry, Monitoring
            # über last_error); sonst Claim freigeben (nächster Lauf nimmt sie erneut).
            new_state = (
                FlashcardSyncEntry.State.FAILED
                if entry.attempts >= max_attempts
                else FlashcardSyncEntry.State.PENDING
            )
            FlashcardSyncEntry.objects.filter(
                pk=entry.pk, state=FlashcardSyncEntry.State.IN_PROGRESS
            ).update(state=new_state, last_error=str(exc)[:2000])
            continue
        # Erfolg SOFORT einzeln durabel machen (Crash danach -> psychosr dedupt via ext_id).
        FlashcardSyncEntry.objects.filter(pk=entry.pk).update(
            state=FlashcardSyncEntry.State.PUSHED, pushed_at=timezone.now(), last_error=""
        )
        pushed += 1

    qs = FlashcardSyncEntry.objects.filter(version_id=version.pk)
    total = qs.count()
    pushed_total = qs.filter(state=FlashcardSyncEntry.State.PUSHED).count()
    failed_perm = qs.filter(state=FlashcardSyncEntry.State.FAILED).count()
    # „offen" = noch (erneut) versuchbar; FAILED zählt NICHT als offen (kein Retry).
    open_count = qs.filter(
        state__in=[FlashcardSyncEntry.State.PENDING, FlashcardSyncEntry.State.IN_PROGRESS]
    ).count()
    _set_synced_marker(document, synced_name, present=(total > 0 and pushed_total == total))

    return {
        "status": "done",
        "document_id": document_id,
        "version_id": version.pk,
        "generated": total,
        "pushed": pushed,
        "failed": failed,
        "open": open_count,
        "failed_permanent": failed_perm,
    }


@shared_task(bind=True, max_retries=5)
def push_document_flashcards(self, document_id: int) -> dict:
    """Erzeugt aus der aktuellen Dokumentversion MC-Lernkarten und pusht sie an
    **psychosr**. Ausgelöst durch den Trigger-Tag (``documents/signals.py``) und –
    für neue Versionen – nach READY aus ``process_document_version``.

    Idempotent über :class:`FlashcardSyncEntry`: Karten werden pro Version einmalig
    generiert, atomar geclaimt und einzeln nach Erfolg als ``pushed`` markiert –
    ein (Teil-)Retry sendet nur die noch offenen. Fehlerbehandlung:

    * noch offene Karten ODER transienter KI-Fehler (``status == "error"``) →
      begrenzter exponentieller Retry;
    * Retries erschöpft oder endgültig fehlgeschlagene Karten (``failed``) →
      der Task endet als **FEHLER** (sichtbar im Monitoring), NICHT als Erfolg.
    """
    result = _sync_document_flashcards(document_id)
    status = result.get("status")
    open_count = result.get("open", 0)
    failed_perm = result.get("failed_permanent", 0)

    # Transient (nochmal versuchen): KI-Providerfehler oder noch offene Karten.
    retryable = status == "error" or open_count > 0
    if retryable and self.request.retries < self.max_retries:
        countdown = min(600, 30 * (2 ** self.request.retries))  # 30,60,120,240,480→max 600
        raise self.retry(
            countdown=countdown,
            exc=RuntimeError(f"psychosr-Sync unvollständig (Dok {document_id}): {result}"),
        )

    # Retries erschöpft oder endgültig fehlgeschlagene Karten -> Task FAILED.
    if retryable or failed_perm:
        logger.error(
            "psychosr-Sync für Dok %s nicht abgeschlossen (retries=%s): %s",
            document_id, self.request.retries, result,
        )
        raise RuntimeError(f"psychosr-Sync fehlgeschlagen (Dok {document_id}): {result}")

    return result


@shared_task
def process_document_version(version_id: int) -> dict:
    """Verarbeitet eine neu angelegte Version bis ``READY``.

    Die fachliche State Machine läuft synchron in ``pipeline.process_version``;
    anschließend werden KI-Metadatenvorschläge asynchron und unverbindlich
    angestoßen.
    """
    version = DocumentVersion.objects.select_related("document").get(pk=version_id)
    result = pipeline.process_version(version)

    # KI-Vorschläge NUR bei erfolgreichem Lauf (done). Bei FAILED/superseded keine
    # verfrühten Vorschläge / unnötigen API-Kosten.
    if result.get("status") == "done":
        from ai.tasks import suggest_document_metadata

        suggest_document_metadata.delay(version.document_id)
        # Trägt das Dokument den psychosr-Trigger-Tag, wird auch die NEUE Version
        # synchronisiert (nicht nur beim erstmaligen Taggen) – nach Commit, damit
        # der Task die persistierte Version/Tags sicher sieht.
        _maybe_dispatch_flashcards(version.document_id)

    # Der semantische Index (Bedeutungssuche + Copilot-RAG) wird bereits innerhalb
    # von pipeline.process_version() über _sync_semantic_index() synchron
    # aufgebaut – kein separater Task nötig (ein einziger Indexierungs-Pfad).
    return result


def _maybe_dispatch_flashcards(document_id: int) -> None:
    """Stößt den psychosr-Sync für ``document_id`` an, WENN psychosr konfiguriert
    ist und das Dokument den Trigger-Tag trägt. Über ``transaction.on_commit``,
    damit der asynchrone Task die committeten Daten sieht. Fehler hier dürfen die
    Verarbeitung nie brechen (best effort).
    """
    if not (getattr(settings, "PSYCHOSR_URL", "") and getattr(settings, "PSYCHOSR_TOKEN", "")):
        return
    from .models import Document

    trigger = getattr(settings, "PSYCHOSR_TRIGGER_TAG", "Psychologie")
    if not Document.objects.filter(pk=document_id, tags__name=trigger).exists():
        return

    def _enqueue():
        try:
            push_document_flashcards.delay(document_id)
        except Exception as exc:  # noqa: BLE001 – Broker weg? Verarbeitung nie brechen
            logger.warning("psychosr-Auto-Sync (neue Version) nicht eingeplant: %s", exc)

    transaction.on_commit(_enqueue)


@shared_task
def reap_stuck_versions() -> dict:
    """Macht Versionen wieder verarbeitbar, die zu lange in einem Zwischenzustand
    hängen.

    Nötig, weil ``acks_late`` bewusst AUS ist (s. settings): ein bei Worker-Crash/
    OOM/Hard-Timeout verlorener Task hinterlässt die Version z. B. in OCR_RUNNING.
    Ohne diesen Watchdog blieb sie dort (das Monitoring zeigte sie nur; der
    Retry-Endpoint akzeptiert nur FAILED). Ab hier gilt „per Retry holbar" wirklich.

    * Zwischenzustände (nicht terminal, nicht SEALED) älter als
      ``PROCESSING_STUCK_AFTER_MINUTES`` -> FAILED (retry-fähig).
    * Hängendes SEALED -> READY: das Dokument IST gesiegelt, nur der letzte
      Übergang fehlte – FAILED wäre falsch (WORM).
    """
    from datetime import timedelta

    from django.utils import timezone

    PS = DocumentVersion.ProcessingState
    minutes = float(getattr(settings, "PROCESSING_STUCK_AFTER_MINUTES", 30))
    threshold = timezone.now() - timedelta(minutes=minutes)

    terminal = {PS.READY, PS.FAILED, PS.SEALED}
    stuck_states = [s for s in PS.values if s not in terminal]

    reaped = 0
    for version in list(
        DocumentVersion.objects.filter(
            processing_state__in=stuck_states,
            processing_state_changed_at__lt=threshold,
        )
    ):
        # Resume-Schritt aus dem HÄNGENDEN Zustand ableiten und als
        # processing_failed_step speichern, damit der Retry ab dort weiterläuft
        # (nicht wieder bei Hashing). Für Zustände ohne eindeutigen Schritt
        # (RETRY_PENDING) den bestehenden Schritt behalten, sonst „hashing".
        resume_step = (
            pipeline.resume_step_for_state(version.processing_state)
            or version.processing_failed_step
            or "hashing"
        )
        try:
            # CAS auf den GELESENEN Zustand+Zeitstempel: hat der Worker inzwischen
            # Fortschritt gemacht (z. B. OCR_RUNNING->OCR_DONE), trifft das Update
            # 0 Zeilen und wir überschreiben den Fortschritt NICHT (-> False).
            if version.mark_processing_failed(
                step=resume_step,
                error=(
                    f"Watchdog: Verarbeitung hängt seit >{minutes:.0f} min "
                    f"(Worker-Crash?), Wiederaufnahme ab '{resume_step}'."
                ),
                expected_state=version.processing_state,
                expected_changed_at=version.processing_state_changed_at,
            ):
                reaped += 1
        except SoftTimeLimitExceeded:
            raise  # Soft-Time-Limit nicht verschlucken.
        except Exception:  # noqa: BLE001 – Watchdog darf pro Version nicht kippen
            logger.exception(
                "reap_stuck_versions: FAILED-Markierung fehlgeschlagen für %s",
                version.id,
            )

    completed = 0
    for version in list(
        DocumentVersion.objects.filter(
            processing_state=PS.SEALED,
            processing_state_changed_at__lt=threshold,
        )
    ):
        try:
            # NICHT direkt SEALED->READY: eine bei seal_version gecrashte Version
            # ist SEALED, aber evtl. NICHT gesiegelt. finalize vervollständigt das
            # Siegel (idempotent) und wechselt erst dann nach READY.
            if pipeline.finalize_sealed_version(version):
                completed += 1
        except SoftTimeLimitExceeded:
            raise  # Soft-Time-Limit nicht verschlucken.
        except Exception:  # noqa: BLE001 – pro Version tolerieren
            logger.exception(
                "reap_stuck_versions: SEALED-Finalisierung fehlgeschlagen für %s",
                version.id,
            )

    if reaped or completed:
        logger.warning(
            "reap_stuck_versions: %d hängende -> FAILED, %d SEALED -> READY.",
            reaped,
            completed,
        )
    return {"reaped": reaped, "completed": completed, "threshold_minutes": minutes}


@shared_task
def reap_stuck_flashcard_syncs() -> dict:
    """Watchdog für hängengebliebene psychosr-Kartensyncs (Beat).

    Nötig, weil ``acks_late`` bewusst AUS ist: geht ``push_document_flashcards``
    bei Worker-Crash/OOM/Hard-Timeout verloren, bleiben Karten in ``pending`` oder
    verwaist in ``in_progress`` liegen – ohne erneuten Tag-Trigger würde sie sonst
    nie wieder jemand senden. Dieser Task findet Versionen mit noch offenen Karten
    (``pending``, oder ``in_progress`` älter als das Claim-Stale-Fenster) und plant
    ``push_document_flashcards`` pro betroffenem Dokument neu ein. ``failed``-Karten
    (endgültig) werden bewusst NICHT erneut versucht (Monitoring über last_error).
    """
    from .models import FlashcardSyncEntry

    if not (getattr(settings, "PSYCHOSR_URL", "") and getattr(settings, "PSYCHOSR_TOKEN", "")):
        return {"redispatched": 0, "disabled": True}

    stale = timedelta(minutes=getattr(settings, "PSYCHOSR_CLAIM_STALE_MINUTES", 15))
    cutoff = timezone.now() - stale
    open_q = Q(state=FlashcardSyncEntry.State.PENDING) | Q(
        state=FlashcardSyncEntry.State.IN_PROGRESS, claimed_at__lt=cutoff
    )
    # Nur aktuelle Versionen re-syncen (der Sync arbeitet auf current_version).
    document_ids = list(
        FlashcardSyncEntry.objects.filter(open_q)
        .filter(version__document__current_version=models.F("version_id"))
        .values_list("version__document_id", flat=True)
        .distinct()
    )
    redispatched = 0
    for doc_id in document_ids:
        try:
            push_document_flashcards.delay(doc_id)
            redispatched += 1
        except SoftTimeLimitExceeded:
            raise
        except Exception:  # noqa: BLE001 – Watchdog darf pro Dokument nicht kippen
            logger.exception("reap_stuck_flashcard_syncs: Enqueue fehlgeschlagen für Dok %s", doc_id)
    if redispatched:
        logger.info("reap_stuck_flashcard_syncs: %d Dokumente neu eingeplant.", redispatched)
    return {"redispatched": redispatched}


def enqueue_processing(version) -> bool:
    """Stößt ``process_document_version`` an; ``True`` bei erfolgreichem Enqueue.

    Gemeinsamer Enqueue-Pfad für ALLE Ingest-Quellen (Upload/Mail/Consume/Import/
    Workbench). Bei einem Broker-Ausfall (Redis down/MISCONF) wird die – bereits
    committete – Version als FAILED am ersten Pipeline-Schritt (``hashing``)
    markiert und ``False`` zurückgegeben, statt sie für immer in UPLOADED hängen
    zu lassen: nur so greift der Retry-Endpoint (der ausschließlich FAILED
    akzeptiert), und ein erneuter Ingest würde sonst an der Dublettenprüfung
    scheitern. Hintergrund-Aufrufer (Mail/Consume) loggen den False-Fall und
    laufen weiter; interaktive Aufrufer (Views) wandeln ihn in ein HTTP 503.
    """
    from kombu.exceptions import OperationalError

    try:
        process_document_version.delay(version.id)
        return True
    except OperationalError:
        from django.core.exceptions import ValidationError

        try:
            version.mark_processing_failed(
                step="hashing", error="Broker nicht erreichbar (Enqueue)"
            )
        except ValidationError:
            pass  # SEALED/READY o. Ä. – Status bleibt unangetastet.
        logger.warning(
            "Enqueue fehlgeschlagen (Broker nicht erreichbar) – Version %s als "
            "FAILED markiert, Retry möglich.",
            version.id,
        )
        return False


@shared_task
def embed_document_version(version_id: int) -> dict:
    """Baut den semantischen Index einer Version neu (Backfill/Async-Reindex).

    Delegiert an ``semantic_index.sync_document_embeddings`` – denselben Kern, den
    auch die Verarbeitungspipeline synchron nutzt. So gibt es genau einen
    Indexierungs-Pfad (Chunking + fastembed + pgvector), idempotent pro Version.
    Genutzt vom ``embed_documents``-Backfill-Command.
    """
    from documents.services import semantic_index

    version = DocumentVersion.objects.select_related("document").get(pk=version_id)
    return semantic_index.sync_document_embeddings(version.document, version=version)


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

    # KI-Vorschläge nur bei erfolgreichem Retry (done); s. process_document_version.
    if result.get("status") == "done":
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
    # Fehlt der Consume-Ordner, tat der Task früher still nichts ({'found': 0})
    # und Ingest lief betrieblich unbemerkt ins Leere (STOAA-321). Stattdessen
    # legen wir das Verzeichnis idempotent an und weisen – nur bei tatsächlicher
    # Neuanlage – EINMAL per WARN darauf hin.
    existed = consume.exists()
    consume.mkdir(parents=True, exist_ok=True)
    if not existed:
        logger.warning("scan_consume_folder: CONSUME_DIR angelegt: %s", consume)

    min_age = float(getattr(settings, "CONSUME_MIN_AGE", 15))
    now = time.time()

    if getattr(settings, "CONSUME_PER_USER", False):
        # Pro-User-Modus: nur die Basis wird vorab angelegt (die eigentlichen
        # Scan-Ordner sind pro-User-Unterordner, deren Namen hier unbekannt
        # sind). ``_processed/``/``_failed/`` entstehen dort je User-Ordner.
        return _scan_per_user(consume, min_age, now)

    # Flat-Modus: ``_processed/``/``_failed/`` direkt unter ``consume`` vorab
    # idempotent anlegen (AK STOAA-321). ``_ingest_consume_dir`` legt sie sonst
    # ohnehin lazy an – hier nur explizit, damit die Ordnerstruktur nach dem
    # ersten Scan vollständig steht.
    (consume / "_processed").mkdir(parents=True, exist_ok=True)
    (consume / "_failed").mkdir(parents=True, exist_ok=True)

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

    Streu-Dateien direkt im Consume-Root (statt in ``<username>/``) werden
    NICHT still verschluckt (STOAA-409): pro Datei WARN-Log + Verschieben nach
    ``_failed/`` auf Root-Ebene, damit jede Datei nachvollziehbar landet und
    das Symptom „Datei verschwindet spurlos" ausgeschlossen ist.
    """
    user_model = get_user_model()

    # ``_failed/`` auf Root-Ebene für fehlplatzierte Streu-Dateien (STOAA-409);
    # lazy angelegt, damit im Normalfall (keine Streu-Dateien) nichts entsteht.
    root_failed_dir = consume / "_failed"

    ingested = []
    skipped = 0
    failed = 0
    deduped = 0
    for entry in sorted(consume.iterdir()):
        if entry.name.startswith(("_", ".")):
            # Interne Ordner (``_processed``/``_failed``) bzw. versteckte
            # Einträge auf Root-Ebene überspringen.
            continue

        if entry.is_file():
            # Fehlplatzierte Datei direkt im Consume-Root: im Pro-User-Modus
            # gehören Dateien in ``<username>/``. Nicht still ignorieren
            # (STOAA-409) → Reife-Check, dann WARN + nach ``_failed/`` (Root).
            try:
                age = now - entry.stat().st_mtime
            except OSError:
                # Datei zwischen ``iterdir`` und ``stat`` verschwunden.
                skipped += 1
                continue
            if age < min_age:
                # Noch nicht fertig geschrieben – nächster Scan versucht erneut.
                skipped += 1
                continue

            logger.warning(
                "scan_consume_folder: Datei %r direkt im Consume-Root im "
                "Pro-User-Modus – Dateien gehören in <username>/. Verschiebe "
                "nach _failed/.",
                entry.name,
            )
            failed += 1
            try:
                # EXDEV-robust wie die übrigen Consume-Moves (STOAA-408):
                # ``_move_into`` fällt bei Mount-Grenzen (NFS/NAS) auf
                # Kopieren+Löschen zurück, statt mit ``os.rename`` an EXDEV zu
                # scheitern und die Datei erneut im Root liegen zu lassen.
                _move_into(entry, root_failed_dir)
            except OSError:
                logger.exception(
                    "scan_consume_folder: Verschieben nach _failed/ "
                    "fehlgeschlagen für %s",
                    entry,
                )
            continue

        if not entry.is_dir():
            # Weder Datei noch Ordner (z. B. Symlink/Socket) – ignorieren.
            continue

        user = user_model.objects.filter(username__iexact=entry.name).first()
        if user is None:
            # Keine stille Fehl-Attribution: unbekannter Ordner wird komplett
            # übersprungen (nicht als owner=None aufgenommen) und protokolliert.
            logger.warning(
                "scan_consume_folder: Unbekannter Benutzer-Ordner %r übersprungen "
                "– kein passender Django-User, keine owner-lose Aufnahme. "
                "Dateien im Pro-User-Modus gehören in <username>/.",
                entry.name,
            )
            continue

        result = _ingest_consume_dir(entry, user, min_age, now)
        ingested.extend(result["ingested"])
        skipped += result["skipped"]
        failed += result["failed"]
        deduped += result.get("deduped", 0)

    return {
        "found": len(ingested),
        "ingested": ingested,
        "skipped": skipped,
        "failed": failed,
        "deduped": deduped,
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

    # Preflight-Diagnose (STOAA-433): Am Live-NFS (Synology) trat wiederholt der
    # Fall auf, dass Dateien aufgenommen wurden (Dokument entsteht), aber NICHT
    # in ``_processed/``/``_failed/`` landeten. Bei ``root_squash``/``all_squash``
    # ohne Schreibrecht des Worker-uid schlägt der Kopier-Teil von
    # ``shutil.move`` (EXDEV-Fallback) still fehl → Datei bleibt im Eingang,
    # während das Dokument schon erzeugt ist. Damit die Ursache nicht länger
    # erraten werden muss, wird pro Scan EINMAL geprüft, ob der laufende Prozess
    # in beiden Zielordnern anlegen+löschen darf; andernfalls loud WARN mit
    # errno und effektiver uid/gid als eindeutiger Log-Beleg.
    _probe_move_targets(processed_dir, failed_dir)

    ingested = []
    skipped = 0
    failed = 0
    deduped = 0
    max_bytes = _consume_max_bytes()
    for entry in sorted(base.iterdir()):
        if entry.name.startswith("."):
            continue

        # Sicherheit (P0): NIE Symlinks dereferenzieren. ``lstat`` folgt dem Link
        # nicht; ein Symlink im Eingang (z. B. auf /proc/self/environ) wird
        # verworfen – dabei wird nur der Link selbst entfernt, nie das Ziel
        # gelesen/kopiert. Nur echte reguläre Dateien werden aufgenommen.
        try:
            st = os.lstat(entry)
        except OSError:
            skipped += 1
            continue
        if stat.S_ISLNK(st.st_mode):
            logger.warning(
                "scan_consume_folder: Symlink im Eingang verworfen (Sicherheits-Schutz): %s",
                entry,
            )
            try:
                entry.unlink()  # entfernt NUR den Link, nie das Ziel
            except OSError:
                pass
            skipped += 1
            continue
        if stat.S_ISDIR(st.st_mode):
            continue
        if not stat.S_ISREG(st.st_mode):
            logger.warning(
                "scan_consume_folder: Nicht-reguläre Datei übersprungen: %s", entry
            )
            skipped += 1
            continue

        # Reife-Check: zu junge (noch nicht fertig geschriebene) Dateien
        # überspringen (mtime aus dem lstat oben, kein zweiter, dereferenzierender
        # stat-Aufruf).
        if now - st.st_mtime < min_age:
            skipped += 1
            continue
        if st.st_size > max_bytes:
            logger.warning(
                "scan_consume_folder: Datei zu groß (%d Bytes) – nach _failed/: %s",
                st.st_size,
                entry,
            )
            _move_into(entry, failed_dir)
            failed += 1
            continue

        try:
            data = _read_regular_nofollow(entry, max_bytes)

            # Dedup-Schutz (STOAA-408): Ein Inhalt, dessen SHA-256 bereits als
            # Version existiert, wird NICHT erneut als Dokument angelegt. Das
            # verhindert Doppel-Dokumente, wenn eine Datei zuvor bereits
            # aufgenommen wurde, aber – etwa wegen eines fehlgeschlagenen Moves
            # über NFS (siehe ``_move_into``) – im Eingang liegengeblieben ist.
            # Der nächste Scan räumt sie dann still nach ``_processed/`` weg.
            sha256_hex = hashlib.sha256(data).hexdigest()
            # Owner-scoped (P1): nur gegen Dokumente DIESES Owners deduplizieren,
            # sonst unterdrückt ein Duplikat bei einem anderen Nutzer den Import.
            if pipeline.find_duplicate_version(sha256_hex, owner=owner) is not None:
                logger.info(
                    "scan_consume_folder: Duplikat (SHA-256 bereits vorhanden) – "
                    "kein Neu-Import, verschiebe nach _processed/: %s",
                    entry,
                )
                _move_into(entry, processed_dir)
                deduped += 1
                continue

            title = entry.stem
            # In den originals-Bereich kopieren, Original aus dem Eingang entfernen.
            storage.ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
            target = _unique(storage.ORIGINALS_DIR / entry.name)
            target.write_bytes(data)

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
            enqueue_processing(version)
            _move_into(entry, processed_dir)
            ingested.append({"document_id": document.id, "title": title})
        except SoftTimeLimitExceeded:
            # Das Soft-Time-Limit darf NICHT vom breiten Catch verschluckt werden
            # (sonst liefe der Task bis zum Hard-Limit weiter). Sauber abbrechen.
            raise
        except Exception:
            # Eine fehlerhafte Datei darf weder den Scan abbrechen noch
            # verschluckt werden: nach ``_failed/`` verschieben + loggen.
            failed += 1
            logger.exception(
                "scan_consume_folder: Verarbeitung fehlgeschlagen für %s", entry
            )
            try:
                _move_into(entry, failed_dir)
            except OSError:
                logger.exception(
                    "scan_consume_folder: Verschieben nach _failed/ fehlgeschlagen für %s",
                    entry,
                )

    return {
        "ingested": ingested,
        "skipped": skipped,
        "failed": failed,
        "deduped": deduped,
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


@shared_task
def check_due_reminders() -> dict:
    """Beat-Task: benachrichtigt einmalig über fällige Wiedervorlagen (STOAA-372).

    Läuft täglich (siehe ``CELERY_BEAT_SCHEDULE``). CEO-Entscheidung
    (STOAA-369): KEIN separates Notification-Modell. Die In-App-Benachrichtigung
    ist die fällig/anstehend-Liste (``/api/reminders/due/``); dieser Beat setzt
    lediglich ``notified_at`` **genau einmal** pro Erinnerung.

    Logik: offene (``done=False``), fällige (``remind_on <= heute``) Erinnerungen
    ohne ``notified_at`` erhalten ``notified_at = now()``. Da anschließend
    ``notified_at__isnull=True`` nicht mehr greift, benachrichtigt ein zweiter
    Lauf dieselbe Erinnerung nicht erneut (Dedupe).

    E-Mail wird nur versendet, wenn SMTP konfiguriert ist
    (``settings.EMAIL_HOST`` gesetzt); fehlt es, wird der Versand still
    übersprungen – **kein** Fehler. Die In-App-Benachrichtigung (``notified_at``
    + due-Liste) funktioniert unabhängig davon.
    """
    from django.core.mail import send_mail

    from .models import DocumentReminder

    today = timezone.localdate()
    smtp_configured = bool(getattr(settings, "EMAIL_HOST", ""))
    now = timezone.now()

    # --- In-App-Benachrichtigung: notified_at GENAU EINMAL setzen -------------
    # Atomarer CAS (filter+update): zwei parallele Beat-Läufe können dieselbe
    # Erinnerung nicht doppelt benachrichtigen – nur der Lauf, dessen UPDATE die
    # Zeile trifft, zählt.
    due_ids = list(
        DocumentReminder.objects.filter(
            done=False, remind_on__lte=today, notified_at__isnull=True
        ).values_list("pk", flat=True)
    )
    notified = 0
    for pk in due_ids:
        claimed = DocumentReminder.objects.filter(
            pk=pk, notified_at__isnull=True
        ).update(notified_at=now)
        if claimed:
            notified += 1

    # --- E-Mail: eigener Versandstatus + atomarer Claim ----------------------
    # email_sent_at ist GETRENNT von notified_at: ein fehlgeschlagener Versand
    # bleibt email_sent_at=NULL und wird beim nächsten Lauf erneut versucht (das
    # In-App-Dedupe blockiert das nicht mehr). Der Claim per select_for_update
    # (skip_locked) verhindert, dass zwei parallele Beats dieselbe Mail senden;
    # email_sent_at wird ERST nach BESTÄTIGTEM Versand (send_mail > 0) gesetzt.
    emailed = 0
    if smtp_configured:
        email_candidates = list(
            DocumentReminder.objects.filter(
                done=False, remind_on__lte=today, email_sent_at__isnull=True
            ).values_list("pk", flat=True)
        )
        for pk in email_candidates:
            try:
                with transaction.atomic():
                    # KEIN select_related hier: select_for_update + Outer Join auf
                    # die NULLBARE FK created_by scheitert auf Postgres („FOR UPDATE
                    # cannot be applied to the nullable side of an outer join"). Die
                    # wenigen Felder (created_by.email, document_id) laden wir direkt.
                    reminder = (
                        DocumentReminder.objects.select_for_update(skip_locked=True)
                        .filter(pk=pk, email_sent_at__isnull=True)
                        .first()
                    )
                    if reminder is None:
                        continue  # anderer Worker hat die Zeile (oder schon versendet)
                    recipient = getattr(reminder.created_by, "email", "") or ""
                    if not recipient:
                        continue  # kein Empfänger -> nichts zu senden (kein Retry nötig)
                    sent = send_mail(
                        subject=f"Wiedervorlage fällig: Dokument #{reminder.document_id}",
                        message=(
                            f"Die Wiedervorlage für Dokument #{reminder.document_id} ist "
                            f"seit {reminder.remind_on.isoformat()} fällig.\n\n"
                            f"{reminder.note}".strip()
                        ),
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                        recipient_list=[recipient],
                        fail_silently=False,  # Fehler wirft -> email_sent_at bleibt NULL (Retry)
                    )
                    if sent:
                        reminder.email_sent_at = now
                        reminder.save(update_fields=["email_sent_at", "updated_at"])
                        emailed += 1
                    # sent == 0: nicht als versendet markieren -> nächster Lauf versucht erneut.
            except SoftTimeLimitExceeded:
                raise  # Soft-Time-Limit nicht verschlucken (s. scan_consume_folder).
            except Exception:
                # Versand best-effort: Fehler loggen, email_sent_at bleibt NULL
                # (Retry beim nächsten Lauf). Der Beat läuft weiter.
                logger.exception(
                    "check_due_reminders: E-Mail-Versand fehlgeschlagen für Reminder %s", pk
                )

    return {"notified": notified, "emailed": emailed}


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


def _probe_move_targets(*dirs: Path) -> None:
    """Prüft einmal pro Scan, ob in den Move-Zielen angelegt+gelöscht werden kann.

    Am NFS/NAS-Consume-Ordner kann der Worker-uid durch ``root_squash``/
    ``all_squash`` das Schreibrecht in ``_processed/``/``_failed/`` fehlen –
    dann bleiben verarbeitete Dateien still im Eingang liegen (STOAA-433).
    Diese Probe erzeugt in dem Fall eine eindeutige WARN-Zeile mit ``errno`` und
    effektiver uid/gid, statt den Fehler erst pro Datei (und ggf. doppelt beim
    ``_failed/``-Fallback) sichtbar zu machen. Rein diagnostisch – ändert das
    Verhalten des Scans nicht.
    """
    for dest_dir in dirs:
        probe = dest_dir / ".stoaa433_write_probe"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            uid = getattr(os, "geteuid", lambda: "n/a")()
            gid = getattr(os, "getegid", lambda: "n/a")()
            logger.warning(
                "scan_consume_folder: Move-Ziel %s NICHT beschreibbar "
                "(errno=%s %s; euid=%s egid=%s). Verarbeitete Dateien können "
                "nicht nach _processed/_failed verschoben werden und bleiben im "
                "Eingang liegen. Vermutlich NFS-Export mit root_squash/all_squash "
                "ohne Schreibrecht des Worker-uid. Export-Mapping (anonuid/anongid) "
                "prüfen (STOAA-433).",
                dest_dir,
                getattr(exc, "errno", "?"),
                exc.strerror or exc,
                uid,
                gid,
            )


def _move_into(src: Path, dest_dir: Path) -> Path:
    """Verschiebt ``src`` robust in ``dest_dir`` (dateisystemübergreifend).

    ``shutil.move`` fällt bei ``EXDEV`` (Quelle und Ziel auf unterschiedlichen
    Geräten/Mounts, wie sie bei NFS/NAS-Consume-Ordnern regelmäßig auftreten)
    auf Kopieren+Löschen zurück – anders als ``Path.rename``/``os.rename``, das
    dann ``OSError: [Errno 18] EXDEV`` wirft. Genau das ließ verarbeitete
    Dateien im Eingang liegen (weder ``_processed/`` noch ``_failed/``), sodass
    der nächste Scan sie erneut aufnahm → Doppel-Dokumente (STOAA-408).
    Namenskollisionen werden per ``_unique`` vermieden.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique(dest_dir / src.name)
    shutil.move(str(src), str(dest))
    return dest
