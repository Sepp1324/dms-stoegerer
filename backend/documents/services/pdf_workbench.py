"""PDF-Werkbank: Seitenoperationen als neue revisionssichere Versionen.

Alle Funktionen arbeiten ausschließlich auf serverseitig bekannten
``DocumentVersion.file_path``-Werten. Nutzer liefern nur Seitenzahlen und
Dokument-IDs; dadurch entsteht keine Möglichkeit, beliebige Dateien auf dem
Server anzusprechen.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterable

import pikepdf
from django.core.exceptions import ValidationError
from pikepdf import Name

from documents import pipeline, storage
from documents.models import AuditLogEntry, Document, DocumentVersion


VALID_ROTATIONS = {0, 90, 180, 270}
ROTATE_NAME = Name("/Rotate")


@dataclass(frozen=True)
class PageSpec:
    page: int
    rotation: int = 0


def page_manifest(version: DocumentVersion) -> dict:
    """Liefert Seitenzahl und vorhandene PDF-Rotation der aktuellen Version."""
    with pikepdf.open(version.file_path) as pdf:
        pages = []
        for idx, page in enumerate(pdf.pages, start=1):
            rotation = int(page.obj.get(ROTATE_NAME, 0) or 0) % 360
            pages.append({"page": idx, "rotation": rotation})
    return {
        "version_id": version.id,
        "version_no": version.version_no,
        "page_count": len(pages),
        "pages": pages,
    }


def render_page_thumbnail(version: DocumentVersion, page_no: int, *, dpi: int = 110) -> bytes:
    """Rendert eine einzelne PDF-Seite als kompaktes JPEG für die Werkbank."""
    count = _page_count(version)
    if page_no < 1 or page_no > count:
        raise ValidationError(f"Seite {page_no} liegt außerhalb von 1..{count}.")

    from pdf2image import convert_from_path

    images = convert_from_path(
        version.file_path,
        dpi=dpi,
        first_page=page_no,
        last_page=page_no,
        fmt="jpeg",
    )
    if not images:
        raise ValidationError(f"Seite {page_no} konnte nicht gerendert werden.")
    image = images[0]
    image.thumbnail((360, 480))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=82, optimize=True)
    return buffer.getvalue()


def rewrite_as_new_version(
    document: Document,
    specs: list[PageSpec],
    *,
    actor,
    reason: str = "",
) -> DocumentVersion:
    """Erzeugt aus Seitenreihenfolge/-Rotation eine neue Version desselben Dokuments."""
    source = _current_pdf(document)
    _validate_specs(source, specs)
    dest = _write_pdf_from_specs([(source, specs)])
    version = pipeline.create_version_for_document(
        document,
        str(dest),
        created_by=actor,
        mime="application/pdf",
        size=dest.stat().st_size,
    )
    AuditLogEntry.objects.create(
        actor=actor,
        action="pdf_workbench_rewrite",
        object_type="Document",
        object_id=str(document.id),
        detail={
            "source_version": source.version_no,
            "new_version": version.version_no,
            "pages": [{"page": item.page, "rotation": item.rotation} for item in specs],
            "reason": reason[:255],
        },
    )
    return version


def merge_as_new_version(
    target: Document,
    documents: Iterable[Document],
    *,
    actor,
    reason: str = "",
) -> DocumentVersion:
    """Merged target + weitere Dokumente in eine neue Version des Ziel-Dokuments."""
    ordered_documents = [target, *list(documents)]
    sources = []
    for document in ordered_documents:
        version = _current_pdf(document)
        count = _page_count(version)
        sources.append((version, [PageSpec(page=i) for i in range(1, count + 1)]))

    dest = _write_pdf_from_specs(sources)
    version = pipeline.create_version_for_document(
        target,
        str(dest),
        created_by=actor,
        mime="application/pdf",
        size=dest.stat().st_size,
    )
    AuditLogEntry.objects.create(
        actor=actor,
        action="pdf_workbench_merge",
        object_type="Document",
        object_id=str(target.id),
        detail={
            "source_documents": [document.id for document in ordered_documents],
            "new_version": version.version_no,
            "reason": reason[:255],
        },
    )
    return version


def split_into_documents(
    source_document: Document,
    parts: list[dict],
    *,
    actor,
) -> list[tuple[Document, DocumentVersion]]:
    """Erzeugt aus Seitenbereichen neue Dokumente und kopiert Kernmetadaten."""
    source = _current_pdf(source_document)
    created = []
    for idx, part in enumerate(parts, start=1):
        title = (part.get("title") or "").strip() or f"{source_document.title} Teil {idx}"
        specs = [PageSpec(page=int(page)) for page in part.get("pages", [])]
        _validate_specs(source, specs)
        dest = _write_pdf_from_specs([(source, specs)])
        document, version = pipeline.create_document_from_file(
            str(dest),
            title=title[:512],
            owner=source_document.owner,
            mime="application/pdf",
            size=dest.stat().st_size,
            ingest_source="workbench",
        )
        _copy_metadata(source_document, document)
        created.append((document, version))

    AuditLogEntry.objects.create(
        actor=actor,
        action="pdf_workbench_split",
        object_type="Document",
        object_id=str(source_document.id),
        detail={
            "source_version": source.version_no,
            "created_documents": [document.id for document, _version in created],
            "parts": [
                {
                    "title": document.title,
                    "pages": parts[idx].get("pages", []),
                    "document": document.id,
                }
                for idx, (document, _version) in enumerate(created)
            ],
        },
    )
    return created


def parse_page_specs(raw_pages) -> list[PageSpec]:
    """Normalisiert API-Payloads für Rewrite: [1] oder [{page, rotation}]."""
    if not isinstance(raw_pages, list) or not raw_pages:
        raise ValidationError("Feld 'pages' muss eine nicht-leere Liste sein.")
    specs = []
    for raw in raw_pages:
        if isinstance(raw, int):
            page_no = raw
            rotation = 0
        elif isinstance(raw, dict):
            page_no = raw.get("page")
            rotation = raw.get("rotation", 0) or 0
        else:
            raise ValidationError("Jede Seite muss eine Zahl oder ein Objekt sein.")
        try:
            page_no = int(page_no)
            rotation = int(rotation)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Seite und Rotation müssen Zahlen sein.") from exc
        if rotation not in VALID_ROTATIONS:
            raise ValidationError("Rotation muss 0, 90, 180 oder 270 sein.")
        specs.append(PageSpec(page=page_no, rotation=rotation))
    return specs


def _current_pdf(document: Document) -> DocumentVersion:
    version = document.current_version
    if version is None:
        raise ValidationError("Dokument hat keine aktuelle Version.")
    if not version.file_path.lower().endswith(".pdf"):
        raise ValidationError("PDF-Werkbank unterstützt aktuell nur PDF-Dateien.")
    return version


def _page_count(version: DocumentVersion) -> int:
    with pikepdf.open(version.file_path) as pdf:
        return len(pdf.pages)


def _validate_specs(version: DocumentVersion, specs: list[PageSpec]) -> None:
    if not specs:
        raise ValidationError("Mindestens eine Seite ist erforderlich.")
    count = _page_count(version)
    for spec in specs:
        if spec.page < 1 or spec.page > count:
            raise ValidationError(f"Seite {spec.page} liegt außerhalb von 1..{count}.")


def _write_pdf_from_specs(sources: list[tuple[DocumentVersion, list[PageSpec]]]):
    out = pikepdf.Pdf.new()
    opened = []
    try:
        for version, specs in sources:
            pdf = pikepdf.open(version.file_path)
            opened.append(pdf)
            for spec in specs:
                out.pages.append(pdf.pages[spec.page - 1])
                page = out.pages[-1]
                if spec.rotation:
                    current = int(page.obj.get(ROTATE_NAME, 0) or 0)
                    page.obj[ROTATE_NAME] = (current + spec.rotation) % 360

        buffer = io.BytesIO()
        out.save(buffer)
        dest, _mime = storage.save_bytes(buffer.getvalue(), ".pdf")
        return dest
    finally:
        out.close()
        for pdf in opened:
            pdf.close()


def _copy_metadata(source: Document, target: Document) -> None:
    target.created_at = source.created_at
    target.correspondent = source.correspondent
    target.document_type = source.document_type
    target.storage_path = source.storage_path
    target.folder = source.folder
    target.case_file = source.case_file
    target.review_status = Document.ReviewStatus.NEEDS_REVIEW
    target.save(
        update_fields=[
            "created_at",
            "correspondent",
            "document_type",
            "storage_path",
            "folder",
            "case_file",
            "review_status",
        ]
    )
    target.tags.set(source.tags.all())
