"""Verarbeitungs-Pipeline für Dokumente.

Reihenfolge (siehe KONZEPT.md §4):
    UPLOADED → HASHED → OCR_RUNNING → OCR_DONE → CLASSIFICATION_RUNNING
    → CLASSIFIED → THUMBNAIL_DONE → SEALED → READY

Die schweren Schritte laufen als Celery-Task (tasks.py); hier stehen die
reinen Funktionen, damit sie testbar und ohne Celery aufrufbar bleiben.
Die OCR selbst ist hinter ``documents.services.ocr`` gekapselt; diese Pipeline
orchestriert Status, Persistenz, Audit und die nachgelagerten Verarbeitungsschritte.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from . import storage
from .models import AuditLogEntry, Document, DocumentVersion
from documents.services.ocr.engine import run_ocr

logger = logging.getLogger(__name__)


def sha256_of(file_path: str | Path) -> str:
    """SHA-256 einer Datei – Baustein von Hash-Kette und Dedup."""
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicate_version(sha256_hex: str) -> DocumentVersion | None:
    """Existierende Version mit identischem Inhalts-Hash (Dedup beim Ingest).

    Grundlage für den Hash-Dedup der E-Mail-Ingestion: gleiche Bytes → gleicher
    SHA-256 → kein Doppel-Import.
    """
    if not sha256_hex:
        return None
    return DocumentVersion.objects.filter(sha256=sha256_hex).first()


def create_document_from_file(
    file_path: str,
    *,
    title: str,
    owner=None,
    mime: str = "",
    size: int | None = None,
) -> tuple[Document, DocumentVersion]:
    """Legt Dokument + erste Version an und protokolliert die Aufnahme.

    Führt (noch) keine OCR aus – das übernimmt die Pipeline anschließend.
    """
    path = Path(file_path)
    document = Document.objects.create(title=title, owner=owner)
    version = DocumentVersion.objects.create(
        document=document,
        version_no=1,
        file_path=str(path),
        mime_type=mime,
        size=size if size is not None else path.stat().st_size,
        created_by=owner,
    )
    document.current_version = version
    document.save(update_fields=["current_version"])

    AuditLogEntry.objects.create(
        actor=owner,
        action="upload",
        object_type="Document",
        object_id=str(document.id),
        detail={"filename": path.name, "size": version.size},
    )
    return document, version


def create_version_for_document(
    document: Document,
    file_path: str,
    *,
    created_by=None,
    mime: str = "",
    size: int | None = None,
) -> DocumentVersion:
    """Hängt eine neue Version an ein bestehendes Dokument (fortlaufende Nr.).

    Setzt die neue Version als ``current_version`` und protokolliert die Aufnahme.
    Hash-Kette (``sha256``/``prev_hash``) füllt anschließend ``process_version``.
    """
    path = Path(file_path)
    last_no = (
        document.versions.order_by("-version_no")
        .values_list("version_no", flat=True)
        .first()
        or 0
    )
    version = DocumentVersion.objects.create(
        document=document,
        version_no=last_no + 1,
        file_path=str(path),
        mime_type=mime,
        size=size if size is not None else path.stat().st_size,
        created_by=created_by,
    )
    document.current_version = version
    document.save(update_fields=["current_version"])

    AuditLogEntry.objects.create(
        actor=created_by,
        action="add_version",
        object_type="Document",
        object_id=str(document.id),
        detail={
            "filename": path.name,
            "size": version.size,
            "version_no": version.version_no,
        },
    )
    return version


def verify_document_integrity(document: Document) -> dict:
    """Prüft die Hash-Kette eines Dokuments – Grundlage der prüfbaren Versionierung.

    Zwei unabhängige Prüfungen je Version:
      * **file_ok** – die Datei auf der Platte wird neu gehasht und mit dem
        gespeicherten ``sha256`` verglichen (Beweis der Unverändertheit).
      * **prev_ok** – der gespeicherte ``prev_hash`` entspricht dem ``sha256``
        der Vorgängerversion (Beweis der lückenlosen Verkettung).

    Rückgabe: ``{"chain_ok": bool, "versions": [ {…}, … ]}`` – aufsteigend nach
    Versionsnummer. ``chain_ok`` ist nur wahr, wenn ALLE Prüfungen bestehen.
    """
    versions = list(document.versions.order_by("version_no"))
    results = []
    chain_ok = True
    prev_sha = ""

    for version in versions:
        source = version.file_path
        file_present = bool(source) and os.path.exists(source)
        computed = sha256_of(source) if file_present else ""
        # Nur prüfbar, wenn ein Hash hinterlegt ist (unverarbeitete Version: offen).
        file_ok = bool(version.sha256) and file_present and computed == version.sha256
        prev_ok = (version.prev_hash or "") == (prev_sha or "")

        if not (file_ok and prev_ok):
            chain_ok = False

        results.append(
            {
                "version_no": version.version_no,
                "sha256": version.sha256,
                "computed_sha256": computed,
                "prev_hash": version.prev_hash,
                "expected_prev_hash": prev_sha,
                "file_present": file_present,
                "file_ok": file_ok,
                "prev_ok": prev_ok,
            }
        )
        prev_sha = version.sha256

    return {"chain_ok": chain_ok, "versions": results}


def extract_text(pdf_path: str | Path) -> str:
    """Extrahiert den gesamten Text eines PDFs via poppler ``pdftotext``."""
    import subprocess

    try:
        result = subprocess.run(
            ["pdftotext", "-q", str(pdf_path), "-"],
            capture_output=True,
            timeout=180,
        )
        return result.stdout.decode("utf-8", errors="ignore")
    except Exception:  # pragma: no cover - Textextraktion ist best effort
        return ""


def generate_thumbnail(version, *, max_width: int = 700) -> str | None:
    """Erzeugt ein JPEG-Miniaturbild der ersten Seite und speichert den Pfad.

    Quelle: bevorzugt das Archiv-PDF, sonst das Original. Für Bild-Originale
    direkt via Pillow. Imports sind lazy, damit das Backend ohne die
    Render-Bibliotheken lädt (z. B. `manage.py check`).
    """
    src = version.archive_path or version.file_path
    if not src or not os.path.exists(src):
        return None

    thumbs_dir = storage.DATA_DIR / "thumbnails"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    dest = thumbs_dir / f"{version.id}.jpg"

    try:
        if src.lower().endswith(".pdf"):
            from pdf2image import convert_from_path

            images = convert_from_path(src, first_page=1, last_page=1, size=(max_width, None))
            if not images:
                return None
            img = images[0].convert("RGB")
        else:
            from PIL import Image

            img = Image.open(src).convert("RGB")
            img.thumbnail((max_width, max_width * 4))
        img.save(dest, "JPEG", quality=80)
    except Exception:  # pragma: no cover - Vorschau ist optional
        return None

    version.thumbnail_path = str(dest)
    version.save(update_fields=["thumbnail_path"])
    return str(dest)


def _page_count(pdf_path: Path) -> int | None:
    try:
        import pikepdf

        with pikepdf.open(pdf_path) as pdf:
            return len(pdf.pages)
    except Exception:  # pragma: no cover - Seitenzahl ist optional
        return None


def hash_version(version: DocumentVersion) -> None:
    """Hash-Kette füllen und State ``HASHED`` setzen."""
    version.sha256 = sha256_of(version.file_path)

    previous = (
        version.document.versions.filter(version_no__lt=version.version_no)
        .order_by("-version_no")
        .first()
    )
    version.prev_hash = previous.sha256 if previous else ""
    version.save(update_fields=["sha256", "prev_hash"])
    version.transition_to(
        DocumentVersion.ProcessingState.HASHED,
        actor=version.created_by,
        detail={"sha256": version.sha256, "prev_hash": version.prev_hash},
    )


def ocr_version(version: DocumentVersion) -> dict:
    """OCR ausführen, technische OCR-Felder speichern und State ``OCR_DONE`` setzen."""
    from .models import OCRStatus

    version.transition_to(
        DocumentVersion.ProcessingState.OCR_RUNNING,
        actor=version.created_by,
    )
    DocumentVersion.objects.filter(pk=version.pk).update(ocr_status=OCRStatus.RUNNING)
    version.ocr_status = OCRStatus.RUNNING

    result = run_ocr(version.file_path)
    archive_candidate = Path(version.file_path).with_suffix(".ocr.pdf")
    archive_path = str(archive_candidate) if archive_candidate.exists() else ""

    version.archive_path = archive_path
    version.ocr_text = result.text
    version.page_count = result.pages
    version.ocr_status = result.status.value
    version.ocr_error = result.error or ""
    version.ocr_engine = result.engine
    version.ocr_duration_ms = result.duration_ms
    version.save(
        update_fields=[
            "archive_path",
            "ocr_text",
            "page_count",
            "ocr_status",
            "ocr_error",
            "ocr_engine",
            "ocr_duration_ms",
        ]
    )
    version.transition_to(
        DocumentVersion.ProcessingState.OCR_DONE,
        actor=version.created_by,
        detail={
            "pages": result.pages,
            "ocr_status": result.status.value,
            "archive_path": archive_path,
            "chars": len(result.text),
        },
    )
    return {
        "pages": result.pages,
        "chars": len(result.text),
        "ocr_status": result.status.value,
        "archive_path": archive_path,
    }


def classify_version(version: DocumentVersion) -> dict:
    """Regelbasierte Klassifizierung ausführen und State ``CLASSIFIED`` setzen."""
    from . import classification

    version.refresh_from_db(fields=["processing_state"])
    version.transition_to(
        DocumentVersion.ProcessingState.CLASSIFICATION_RUNNING,
        actor=version.created_by,
    )
    result = classification.apply_rules(version.document)
    version.document.refresh_from_db(fields=["classification"])
    version.transition_to(
        DocumentVersion.ProcessingState.CLASSIFIED,
        actor=version.created_by,
        detail={"classification": version.document.classification},
    )
    return result


def generate_version_thumbnail(version: DocumentVersion) -> str | None:
    """Miniaturbild erzeugen und State ``THUMBNAIL_DONE`` setzen."""
    thumbnail_path = generate_thumbnail(version)
    version.refresh_from_db(fields=["thumbnail_path", "processing_state"])
    version.transition_to(
        DocumentVersion.ProcessingState.THUMBNAIL_DONE,
        actor=version.created_by,
        detail={"thumbnail_path": thumbnail_path or ""},
    )
    return thumbnail_path


def seal_version(version: DocumentVersion) -> None:
    """WORM-/Retention-Siegel setzen und danach State ``READY`` erreichen."""
    version.refresh_from_db(fields=["processing_state"])
    version.transition_to(
        DocumentVersion.ProcessingState.SEALED,
        actor=version.created_by,
    )
    _seal_version(version)
    version.transition_to(
        DocumentVersion.ProcessingState.READY,
        actor=version.created_by,
    )


def process_version(version: DocumentVersion) -> dict:
    """Vollständige Verarbeitung einer Version entlang der State Machine."""
    hash_version(version)
    ocr_result = ocr_version(version)
    classify_version(version)
    generate_version_thumbnail(version)
    AuditLogEntry.objects.create(
        actor=version.created_by,
        action="ocr",
        object_type="DocumentVersion",
        object_id=str(version.id),
        detail={
            "pages": ocr_result["pages"],
            "sha256": version.sha256,
            "archive_path": ocr_result["archive_path"],
            "ocr_status": ocr_result["ocr_status"],
            "chars": ocr_result["chars"],
        },
    )
    seal_version(version)

    return {
        "version_id": version.id,
        "sha256": version.sha256,
        "pages": ocr_result["pages"],
        "chars": ocr_result["chars"],
        "processing_state": DocumentVersion.ProcessingState.READY,
        "status": "done",
    }


def _add_months(d, months: int):
    """Addiert `months` Monate zu einem date-Objekt (kein dateutil nötig)."""
    import calendar
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    from datetime import date
    return date(year, month, day)


def _seal_version(version: DocumentVersion) -> None:
    """Setzt is_immutable=True und schützt die Archiv-Datei (chmod 0444)."""
    import os as _os
    from datetime import date

    # Archiv-Datei schreibschützen
    archive = version.archive_path or version.file_path
    if archive:
        try:
            _os.chmod(archive, 0o444)
        except OSError:
            pass  # Im Test/Mock-Umfeld ggf. kein echtes Dateisystem

    # Aufbewahrungsfrist aus DocumentType berechnen
    retention_until = None
    doc_type = version.document.document_type
    if doc_type and doc_type.retention_months:
        ref = version.document.created_at or version.document.added_at
        base = ref.date() if hasattr(ref, "date") else date.today()
        retention_until = _add_months(base, doc_type.retention_months)

    # Direkt auf DB-Ebene setzen, ohne save()-Guard auszulösen
    DocumentVersion.objects.filter(pk=version.pk).update(
        is_immutable=True,
        retention_until=retention_until,
    )
    version.is_immutable = True
    version.retention_until = retention_until

    # Retention auch am Dokument speichern (längste Frist gewinnt)
    doc = version.document
    if retention_until and (doc.retention_until is None or retention_until > doc.retention_until):
        from .models import Document
        Document.objects.filter(pk=doc.pk).update(retention_until=retention_until)
        doc.retention_until = retention_until

    AuditLogEntry.objects.create(
        actor=version.created_by,
        action="immutable_set",
        object_type="DocumentVersion",
        object_id=str(version.id),
        detail={"archive_path": archive, "retention_until": str(retention_until)},
    )
