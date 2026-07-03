"""Verarbeitungs-Pipeline für Dokumente.

Reihenfolge (siehe KONZEPT.md §4):
    Datei rein → Hash bilden → OCR (→ PDF/A + Text) → Metadaten → Ablage → Audit

Die schweren Schritte laufen als Celery-Task (tasks.py); hier stehen die
reinen Funktionen, damit sie testbar und ohne Celery aufrufbar bleiben.
Der ``ocrmypdf``-Import ist bewusst *lazy* (erst in der Funktion), damit das
Backend auch ohne installierte OCR-Binaries lädt (z. B. für `manage.py check`).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from . import storage
from .models import AuditLogEntry, Document, DocumentVersion


def sha256_of(file_path: str | Path) -> str:
    """SHA-256 einer Datei – Baustein von Hash-Kette und Dedup."""
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def run_ocr(input_path: str | Path, output_path: Path) -> tuple[str, int | None]:
    """Erzeugt aus der Eingabe ein durchsuchbares PDF/A und liefert (Text, Seiten).

    Nutzt OCRmyPDF (Tesseract, deu+eng). Bereits vorhandene Textebenen werden
    übersprungen (``skip_text``), Bilder werden – bei installiertem img2pdf –
    automatisch als PDF verarbeitet.
    """
    import ocrmypdf

    output_path.parent.mkdir(parents=True, exist_ok=True)

    ocrmypdf.ocr(
        str(input_path),
        str(output_path),
        output_type="pdfa",
        language="deu+eng",
        skip_text=True,
        progress_bar=False,
    )

    # Text aus dem fertigen PDF/A ziehen – erfasst native UND OCR'te Textebenen.
    # (Das ocrmypdf-Sidecar bliebe bei *digitalen* PDFs leer, weil skip_text die
    #  OCR überspringt und nur OCR-Ausgabe ins Sidecar schreibt.)
    text = extract_text(output_path)
    pages = _page_count(output_path)
    return text, pages


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


def process_version(version: DocumentVersion) -> dict:
    """Vollständige Verarbeitung einer Version: Hash-Kette + OCR + Ablage + Audit."""
    version.sha256 = sha256_of(version.file_path)

    previous = (
        version.document.versions.filter(version_no__lt=version.version_no)
        .order_by("-version_no")
        .first()
    )
    version.prev_hash = previous.sha256 if previous else ""

    archive_path = storage.build_archive_path(version.document)
    text, pages = run_ocr(version.file_path, archive_path)

    version.archive_path = str(archive_path)
    version.ocr_text = text
    version.page_count = pages
    version.save(
        update_fields=[
            "sha256",
            "prev_hash",
            "archive_path",
            "ocr_text",
            "page_count",
        ]
    )

    # Miniaturbild der ersten Seite erzeugen (setzt thumbnail_path selbst).
    generate_thumbnail(version)

    AuditLogEntry.objects.create(
        actor=version.created_by,
        action="ocr",
        object_type="DocumentVersion",
        object_id=str(version.id),
        detail={
            "pages": pages,
            "sha256": version.sha256,
            "archive_path": version.archive_path,
            "chars": len(text),
        },
    )
    return {
        "version_id": version.id,
        "sha256": version.sha256,
        "pages": pages,
        "chars": len(text),
        "status": "done",
    }
