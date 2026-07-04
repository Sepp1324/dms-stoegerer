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
    """Nimmt alle reifen Dateien aus dem Consume-Ordner auf und startet die Pipeline.

    NFS-tauglich: Eine Datei wird erst verarbeitet, wenn sie mindestens
    ``settings.CONSUME_MIN_AGE`` Sekunden alt und nicht leer ist – so werden
    noch im Schreiben befindliche bzw. über NFS langsam abgelegte Dateien in
    einem späteren Scan gegriffen statt teilweise gelesen (zustandsloser
    Reife-Check über ``st_mtime``; ``CONSUME_MIN_AGE=0`` schaltet ihn ab).

    Verarbeitete Dateien werden nach ``consume/_processed/`` verschoben, damit
    sie nicht doppelt aufgenommen werden. Schlägt die Verarbeitung einer Datei
    fehl, wandert sie nach ``consume/_failed/`` (Quarantäne) und der Scan läuft
    mit der nächsten Datei weiter – ein Fehler bricht weder den Scan ab, noch
    verschluckt er die Datei, noch führt er zu einem Endlos-Retry.
    """
    consume = storage.CONSUME_DIR
    if not consume.exists():
        return {"found": 0, "ingested": []}

    processed_dir = consume / "_processed"
    failed_dir = consume / "_failed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    min_age = getattr(settings, "CONSUME_MIN_AGE", 15)

    ingested = []
    for entry in sorted(consume.iterdir()):
        if entry.is_dir() or entry.name.startswith("."):
            continue
        # NFS-Reife-Check: zu junge oder (noch) leere Dateien überspringen –
        # der nächste Scan greift sie, sobald sie zur Ruhe gekommen sind.
        try:
            info = entry.stat()
        except OSError:
            # Datei verschwand zwischen iterdir() und stat() (Race) – überspringen.
            continue
        if min_age > 0 and (time.time() - info.st_mtime < min_age or info.st_size == 0):
            continue

        # Pro-Datei-Verarbeitung kapseln: ein Fehler quarantänisiert nur diese
        # Datei und stoppt nicht den ganzen Scan.
        try:
            record = _ingest_consume_file(entry)
        except Exception:
            logger.exception(
                "Consume-Datei %s fehlgeschlagen – Quarantäne", entry.name
            )
            failed_dir.mkdir(parents=True, exist_ok=True)
            try:
                entry.rename(_unique(failed_dir / entry.name))
            except OSError:
                logger.exception(
                    "Verschieben nach _failed fehlgeschlagen: %s", entry.name
                )
            continue

        entry.rename(_unique(processed_dir / entry.name))
        ingested.append(record)

    return {"found": len(ingested), "ingested": ingested}


def _ingest_consume_file(entry: Path) -> dict:
    """Nimmt eine einzelne Consume-Datei auf und stößt die Pipeline an.

    Kapselt den fehleranfälligen Teil (Kopie, Dedup, Anlage) für die
    Quarantäne-Logik von ``scan_consume_folder``.
    """
    title = entry.stem
    # In den originals-Bereich kopieren; das Original bleibt vorerst liegen und
    # wird erst vom Aufrufer nach _processed verschoben, wenn alles glückt.
    storage.ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    target = _unique(storage.ORIGINALS_DIR / entry.name)
    target.write_bytes(entry.read_bytes())

    # SHA-256-Dedup: identische Bytes eines bereits verarbeiteten Dokuments
    # nicht ein zweites Mal aufnehmen (ergänzt die _processed-Idempotenz).
    existing = pipeline.find_duplicate_version(pipeline.sha256_of(target))
    if existing is not None:
        target.unlink(missing_ok=True)
        return {"document_id": existing.document_id, "title": title, "duplicate": True}

    document, version = pipeline.create_document_from_file(
        str(target), title=title, size=target.stat().st_size
    )
    process_document_version.delay(version.id)
    return {"document_id": document.id, "title": title}


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
