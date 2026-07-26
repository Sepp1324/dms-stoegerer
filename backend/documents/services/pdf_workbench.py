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


def merge_max_source_documents() -> int:
    """Max. ZUSÄTZLICHE Dokumente für einen Merge (ohne das Zieldokument).

    Off-by-one-Fix (P3): Der Service zählt beim Merge das Zieldokument mit
    (``[target, *sources]``) gegen ``_max_documents()``. Der View muss die
    ``document_ids`` daher gegen ``_max_documents() - 1`` deckeln, sonst würde ein
    Payload mit genau ``_max_documents()`` IDs den View passieren, aber im Service
    (Ziel + IDs = Limit + 1) mit einer ANDEREN Meldung scheitern – nach Aufbau.
    """
    return max(0, _max_documents() - 1)


class StaleWorkbenchVersion(Exception):
    """Die Werkbank-Aktion basiert auf einer inzwischen veralteten Quellversion.

    Der Client sendet die Version, auf der sein Seiten-Manifest beruht
    (``source_version_id``). Hat eine parallele Aktion inzwischen eine neue
    aktuelle Version erzeugt, würde die Werkbank eine spätere Version auf Basis
    eines veralteten Originals erzeugen und die parallele Version überschreiben.
    Der View übersetzt das in ``409 Conflict``.
    """


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


def _thumbnail_render_timeout() -> int:
    """Sekunden-Timeout für das Poppler-Rendern einer Miniatur (P1)."""
    return int(getattr(settings, "PDF_THUMBNAIL_TIMEOUT_SECONDS", 20))


def _thumbnail_cache_path(version: DocumentVersion, page_no: int, dpi: int):
    """Disk-Cache-Pfad einer Miniatur. Version-/seitenbasiert – der Seiteninhalt
    einer Version ist unveränderlich, daher ist der Cache dauerhaft gültig."""
    return (
        storage.DATA_DIR
        / "cache"
        / "workbench_thumbs"
        / str(version.id)
        / f"{page_no}_{dpi}.jpg"
    )


def render_page_thumbnail(version: DocumentVersion, page_no: int, *, dpi: int = 110) -> bytes:
    """Rendert eine einzelne PDF-Seite als kompaktes JPEG für die Werkbank.

    Server-Cache (P1): Ein bereits gerendertes (version, page, dpi)-JPEG wird von
    der Platte gelesen, statt Poppler erneut auszuführen – so kostet ein
    Neu-Anfordern (mehrere Tabs, Reload, direkte API-Aufrufe) keinen weiteren
    Renderprozess. Das Rendern selbst läuft mit hartem Timeout.
    """
    cache_path = _thumbnail_cache_path(version, page_no, dpi)
    try:
        if cache_path.exists():
            return cache_path.read_bytes()
    except OSError:
        pass  # Cache ist best-effort – bei Lesefehler regulär rendern.

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
        timeout=_thumbnail_render_timeout(),
    )
    if not images:
        raise ValidationError(f"Seite {page_no} konnte nicht gerendert werden.")
    image = images[0]
    image.thumbnail((360, 480))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=82, optimize=True)
    data = buffer.getvalue()

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
    except OSError:
        pass  # Cache-Schreibfehler darf die Antwort nicht verhindern.
    return data


def _ensure_current_version(document: Document, expected_version_id) -> None:
    """Wirft StaleWorkbenchVersion, wenn ``expected_version_id`` nicht (mehr) die
    aktuelle Version des Dokuments ist. Frühe, billige Prüfung vor dem Aufbau."""
    if expected_version_id is None:
        return
    if document.current_version_id != int(expected_version_id):
        raise StaleWorkbenchVersion(
            "Das Dokument wurde zwischenzeitlich geändert – bitte neu laden."
        )


def rewrite_as_new_version(
    document: Document,
    specs: list[PageSpec],
    *,
    actor,
    reason: str = "",
    expected_version_id=None,
) -> DocumentVersion:
    """Erzeugt aus Seitenreihenfolge/-Rotation eine neue Version desselben Dokuments."""
    source = _current_pdf(document)
    # Frühe Konfliktprüfung (P2), spart den Aufbau bei bereits veralteter Version.
    _ensure_current_version(document, expected_version_id)
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
            # Zeilensperre + erneuter Vergleich (P2, Race-frei): zwischen der frühen
            # Prüfung und hier könnte eine parallele Aktion die aktuelle Version
            # geändert haben – dann NICHT überschreiben, sondern 409.
            locked = Document.objects.select_for_update().get(pk=document.pk)
            if locked.current_version_id != source.id:
                raise StaleWorkbenchVersion(
                    "Das Dokument wurde zwischenzeitlich geändert – bitte neu laden."
                )
            version = pipeline.create_version_for_document(
                locked,
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
    expected_version_id=None,
) -> DocumentVersion:
    """Merged target + weitere Dokumente in eine neue Version des Ziel-Dokuments."""
    target_source = _current_pdf(target)
    # Frühe Konfliktprüfung auf das Zieldokument (P2).
    _ensure_current_version(target, expected_version_id)
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
            # Zeilensperre + erneuter Vergleich auf das Ziel (P2, Race-frei).
            locked = Document.objects.select_for_update().get(pk=target.pk)
            if locked.current_version_id != target_source.id:
                raise StaleWorkbenchVersion(
                    "Das Dokument wurde zwischenzeitlich geändert – bitte neu laden."
                )
            version = pipeline.create_version_for_document(
                locked,
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

    # Ressourcengrenzen ZUERST und BILLIG prüfen (P1):
    #  * Teileanzahl vor der Schleife (je Teil ein neues Dokument),
    #  * Seitenzahl der Quelle GENAU EINMAL bestimmen (statt _validate_specs, das
    #    das PDF je Teil erneut öffnete – tausende Öffnungen bei grossem Payload),
    #  * kumuliertes Seitenlimit WÄHREND der Schleife abbrechen.
    if len(parts) > _max_documents():
        raise ValidationError(
            f"Zu viele Teile ({len(parts)} > Limit {_max_documents()})."
        )
    source_page_count = _page_count(source)
    max_pages = _max_pages()

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
        if not specs:
            raise ValidationError(f"Teil {idx}: Mindestens eine Seite ist erforderlich.")
        for spec in specs:
            if spec.page < 1 or spec.page > source_page_count:
                raise ValidationError(
                    f"Teil {idx}: Seite {spec.page} liegt außerhalb von "
                    f"1..{source_page_count}."
                )
        total_pages += len(specs)
        if total_pages > max_pages:
            raise ValidationError(
                f"Zu viele Seiten insgesamt (> Limit {max_pages})."
            )
        prepared.append((title, specs))

    # Eingabegröße der Quelle prüfen (Bytes-Limit); Seiten/Anzahl sind oben schon
    # billig geprüft.
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
