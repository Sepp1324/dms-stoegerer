"""Celery-Tasks der Verarbeitungs-Pipeline (asynchron, außerhalb des Requests)."""
from pathlib import Path

from celery import shared_task

from . import pipeline, storage
from .models import DocumentVersion


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
    """Nimmt alle Dateien aus dem Consume-Ordner auf und stößt die Pipeline an.

    Verarbeitete Dateien werden nach ``consume/_processed/`` verschoben, damit
    sie nicht doppelt aufgenommen werden. (Beat-Zeitplan folgt später.)
    """
    consume = storage.CONSUME_DIR
    if not consume.exists():
        return {"found": 0, "ingested": []}

    processed_dir = consume / "_processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    ingested = []
    for entry in sorted(consume.iterdir()):
        if entry.is_dir() or entry.name.startswith("."):
            continue
        title = entry.stem
        # In den originals-Bereich kopieren, Original aus dem Eingang entfernen.
        storage.ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
        target = storage.ORIGINALS_DIR / entry.name
        target = _unique(target)
        target.write_bytes(entry.read_bytes())

        document, version = pipeline.create_document_from_file(
            str(target), title=title, size=target.stat().st_size
        )
        process_document_version.delay(version.id)
        entry.rename(processed_dir / entry.name)
        ingested.append({"document_id": document.id, "title": title})

    return {"found": len(ingested), "ingested": ingested}


def _unique(path: Path) -> Path:
    counter = 1
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        counter += 1
    return candidate
