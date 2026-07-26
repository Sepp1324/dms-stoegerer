"""PDF-Werkbank: Seitenoperationen als neue revisionssichere Versionen.

Alle Funktionen arbeiten ausschließlich auf serverseitig bekannten
``DocumentVersion.file_path``-Werten. Nutzer liefern nur Seitenzahlen und
Dokument-IDs; dadurch entsteht keine Möglichkeit, beliebige Dateien auf dem
Server anzusprechen.
"""
from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass
from typing import Iterable

import pikepdf
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from pikepdf import Name

from documents import pipeline, storage
from documents.models import AuditLogEntry, Document, DocumentVersion


VALID_ROTATIONS = {0, 90, 180, 270}
ROTATE_NAME = Name("/Rotate")


# Ressourcengrenzen (P1, DoS/OOM-Schutz). Merge/Split akzeptierten bislang
# beliebig viele Dokumente/Seiten; ein grosser Vorgang konnte den Backend-Pod per
# OOM beenden. Grenzen sind per Settings anpassbar.
def _max_documents() -> int:
    return int(getattr(settings, "PDF_WORKBENCH_MAX_DOCUMENTS", 50))


def merge_max_documents() -> int:
    """Öffentliches Limit für die Merge-Payload (Views deckeln damit früh ab)."""
    return _max_documents()


def _max_pages() -> int:
    return int(getattr(settings, "PDF_WORKBENCH_MAX_PAGES", 2000))


def _max_input_bytes() -> int:
    return int(getattr(settings, "PDF_WORKBENCH_MAX_INPUT_MB", 500)) * 1024 * 1024


def _enforce_source_limits(versions: list[DocumentVersion], *, total_pages: int) -> None:
    """Wirft ValidationError, wenn ein Vorgang die Ressourcengrenzen sprengt."""
    if len(versions) > _max_documents():
        raise ValidationError(
            f"Zu viele Dokumente ({len(versions)} > Limit {_max_documents()})."
        )
    if total_pages > _max_pages():
        raise ValidationError(
            f"Zu viele Seiten ({total_pages} > Limit {_max_pages()})."
        )
    total_bytes = 0
    for version in versions:
        try:
            total_bytes += os.path.getsize(version.file_path)
        except OSError:
            continue
    if total_bytes > _max_input_bytes():
        raise ValidationError(
            f"Eingabegröße zu groß ({total_bytes} Bytes > Limit {_max_input_bytes()})."
        )


def _unlink_quietly(path) -> None:
    """Entfernt eine Datei, ohne bei Fehlern zu werfen (Cleanup nach Rollback)."""
    try:
        os.unlink(path)
    except OSError:
        pass


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
    # Ressourcengrenzen AUCH beim Rewrite prüfen (P1): sonst könnte eine direkte
    # API-Anfrage dieselbe Quellseite tausendfach wiederholen und erst nach dem
    # vollständigen PDF-Aufbau am 200-MB-Limit scheitern (OOM-Risiko davor).
    _enforce_source_limits([source], total_pages=len(specs))
    dest = _write_pdf_from_specs([(source, specs)])
    # Version + Audit als EINE Operation (P2): scheitert der Audit nach dem
    # Versions-Insert, bliebe sonst eine neue aktuelle Version bestehen, obwohl die
    # API 400 liefert und nichts einreiht. Bei Rollback wird die PDF-Datei entfernt.
    try:
        with transaction.atomic():
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
                    "pages": [
                        {"page": item.page, "rotation": item.rotation} for item in specs
                    ],
                    "reason": reason[:255],
                },
            )
    except Exception:
        _unlink_quietly(dest)
        raise
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

    # Ressourcengrenzen VOR dem Aufbau prüfen (P1, OOM-Schutz).
    _enforce_source_limits(
        [version for version, _specs in sources],
        total_pages=sum(len(specs) for _version, specs in sources),
    )

    dest = _write_pdf_from_specs(sources)
    # Version + Audit atomar (P2), Datei-Cleanup bei Rollback – wie beim Rewrite.
    try:
        with transaction.atomic():
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
                    "source_documents": [doc.id for doc in ordered_documents],
                    "new_version": version.version_no,
                    "reason": reason[:255],
                },
            )
    except Exception:
        _unlink_quietly(dest)
        raise
    return version


def split_into_documents(
    source_document: Document,
    parts: list[dict],
    *,
    actor,
) -> list[tuple[Document, DocumentVersion]]:
    """Erzeugt aus Seitenbereichen neue Dokumente und kopiert Kernmetadaten."""
    source = _current_pdf(source_document)

    # 1) ALLE Teile ZUERST vollständig validieren (P1): Bislang wurden die Teile
    # nacheinander validiert UND gespeichert – war Teil 1 gültig und Teil 2
    # ungültig, blieb Teil 1 als Dokument bestehen (ein erneuter Versuch erzeugte
    # Duplikate). Jetzt entsteht bei einem ungültigen Teil KEIN Erzeugnis.
    prepared: list[tuple[str, list[PageSpec]]] = []
    total_pages = 0
    for idx, part in enumerate(parts, start=1):
        title = (part.get("title") or "").strip() or f"{source_document.title} Teil {idx}"
        try:
            specs = [PageSpec(page=int(page)) for page in part.get("pages", [])]
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Teil {idx}: Seiten müssen Zahlen sein.") from exc
        _validate_specs(source, specs)
        total_pages += len(specs)
        prepared.append((title, specs))

    # Ressourcengrenzen (P1, OOM-Schutz): je Teil ein neues Dokument.
    if len(prepared) > _max_documents():
        raise ValidationError(
            f"Zu viele Teile ({len(prepared)} > Limit {_max_documents()})."
        )
    _enforce_source_limits([source], total_pages=total_pages)

    # 2) Erst nach vollständiger Validierung erzeugen – in EINER äußeren
    # Transaktion; bei einem Fehler werden die bereits geschriebenen PDFs entfernt
    # (der DB-Rollback allein liesse sie verwaisen).
    created: list[tuple[Document, DocumentVersion]] = []
    written_files: list = []
    try:
        with transaction.atomic():
            for title, specs in prepared:
                dest = _write_pdf_from_specs([(source, specs)])
                written_files.append(dest)
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
                    "created_documents": [doc.id for doc, _v in created],
                    "parts": [
                        {
                            "title": doc.title,
                            "pages": [spec.page for spec in prepared[idx][1]],
                            "document": doc.id,
                        }
                        for idx, (doc, _v) in enumerate(created)
                    ],
                },
            )
    except Exception:
        for path in written_files:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise
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
    # Das Ergebnis-PDF wird direkt in eine TEMP-DATEI geschrieben statt in ein
    # BytesIO mit anschliessendem getvalue() (P1): Letzteres hielt das fertige PDF
    # doppelt komplett im RAM -> OOM-Risiko bei grossen Vorgaengen. pikepdf.save()
    # streamt auf die Platte; storage.save_file uebernimmt die Datei ohne
    # Voll-Read in den Speicher.
    out = pikepdf.Pdf.new()
    opened = []
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_path = tmp.name
    tmp.close()
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

        out.save(tmp_path)
        dest, _mime = storage.save_file(tmp_path)  # verschiebt die Temp-Datei
        return dest
    finally:
        out.close()
        for pdf in opened:
            pdf.close()
        # save_file() hat die Temp-Datei verschoben; nur bei einem Fehler davor
        # ist noch aufzuraeumen.
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


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
