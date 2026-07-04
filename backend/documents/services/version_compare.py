"""Versionsvergleich-Service (STOAA-288).

Alle Vergleichslogik liegt hier – keine Logik in Models, ViewSets oder Serializern.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from documents.models import Document, DocumentVersion


# ---------------------------------------------------------------------------
# Result-Datenklassen
# ---------------------------------------------------------------------------

@dataclass
class FileDiff:
    old_sha256: str
    new_sha256: str
    old_size: int
    new_size: int
    old_mime: str
    new_mime: str
    changed: bool
    # PDF-Stufe-2-Vorbereitung
    old_page_count: int | None
    new_page_count: int | None
    pages_changed: bool


@dataclass
class TagDiff:
    added: list[str]
    removed: list[str]


@dataclass
class FieldChange:
    old: Any
    new: Any


@dataclass
class CompareSummary:
    text_changed: bool
    metadata_changed: bool
    tags_changed: bool
    custom_fields_changed: bool
    binary_changed: bool
    pages_changed: bool
    tag_changes: int
    field_changes: int


@dataclass
class VersionCompareResult:
    document: int
    from_version: int
    to_version: int
    summary: CompareSummary
    text_diff: str
    text_diff_html: str
    metadata: dict[str, FieldChange]
    tags: TagDiff
    custom_fields: dict[str, FieldChange]
    files: FileDiff
    # Stufe 1: Metadaten/Tags/Custom-Fields sind (noch) NICHT pro Version
    # versioniert (siehe STOAA-288-Machbarkeitsbefund). Diese Sektionen bleiben
    # in Stufe 1 leer; das Flag signalisiert dem Frontend die Ursache und hält
    # die Antwort-Shape stabil, damit Stufe 2 rein additiv andockt.
    metadata_versioning_supported: bool = False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class VersionCompareService:
    """Vergleicht zwei DocumentVersions desselben Dokuments."""

    # Metadaten-Felder die verglichen werden (Attributname → Label)
    METADATA_FIELDS: dict[str, str] = {
        "document__title": "title",
        "document__document_type__name": "document_type",
        "document__correspondent__name": "correspondent",
        "document__storage_path__name": "storage_path",
        "document__owner__username": "owner",
        "document__retention_until": "retention_until",
        "document__status": "status",
    }

    def compare(
        self,
        document: Document,
        from_no: int,
        to_no: int,
    ) -> VersionCompareResult:
        """Lädt beide Versionen und berechnet den vollständigen Diff."""
        v_from = self._load_version(document, from_no)
        v_to = self._load_version(document, to_no)

        text_diff = self._text_diff(v_from.ocr_text, v_to.ocr_text)
        text_diff_html = self._text_diff_html(
            v_from.ocr_text, v_to.ocr_text, from_no, to_no
        )
        metadata_diff = self._metadata_diff(v_from, v_to)
        tags_diff = self._tags_diff(v_from, v_to)
        custom_diff = self._custom_fields_diff(v_from, v_to)
        file_diff = self._file_diff(v_from, v_to)

        summary = CompareSummary(
            text_changed=bool(text_diff),
            metadata_changed=bool(metadata_diff),
            tags_changed=bool(tags_diff.added or tags_diff.removed),
            custom_fields_changed=bool(custom_diff),
            binary_changed=file_diff.changed,
            pages_changed=file_diff.pages_changed,
            tag_changes=len(tags_diff.added) + len(tags_diff.removed),
            field_changes=len(custom_diff),
        )

        return VersionCompareResult(
            document=document.pk,
            from_version=from_no,
            to_version=to_no,
            summary=summary,
            text_diff=text_diff,
            text_diff_html=text_diff_html,
            metadata=metadata_diff,
            tags=tags_diff,
            custom_fields=custom_diff,
            files=file_diff,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_version(document: Document, version_no: int) -> DocumentVersion:
        """Lädt eine Version mit allen nötigen Relations in einer Query."""
        return (
            DocumentVersion.objects.select_related(
                "document",
                "document__document_type",
                "document__correspondent",
                "document__storage_path",
                "document__owner",
            )
            .prefetch_related(
                "document__tags",
                "document__custom_field_values__field",
            )
            .get(document=document, version_no=version_no)
        )

    @staticmethod
    def _text_diff(old_text: str, new_text: str) -> str:
        """unified_diff der OCR-Texte; leer wenn gleich."""
        if old_text == new_text:
            return ""
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="Vorherige Version",
            tofile="Neue Version",
        )
        return "".join(diff)

    @staticmethod
    def _text_diff_html(
        old_text: str, new_text: str, from_no: int, to_no: int
    ) -> str:
        """Side-by-Side-HTML-Tabelle der OCR-Texte (difflib.HtmlDiff).

        Leer, wenn beide Texte identisch sind – das Frontend zeigt dann keinen
        Diff-Block. Die Tabelle ist direkt einbettbar (nur ``<table class="diff">``,
        ohne umschließendes Dokument); das Styling übernimmt das Frontend-Theme.
        """
        if old_text == new_text:
            return ""
        return difflib.HtmlDiff().make_table(
            old_text.splitlines(),
            new_text.splitlines(),
            fromdesc=f"Version {from_no}",
            todesc=f"Version {to_no}",
            context=True,
            numlines=3,
        )

    @staticmethod
    def _metadata_diff(
        v_from: DocumentVersion, v_to: DocumentVersion
    ) -> dict[str, FieldChange]:
        """Vergleicht Metadaten; gibt nur geänderte Felder zurück."""
        doc_from = v_from.document
        doc_to = v_to.document

        def _val(doc: Document, attr: str) -> Any:
            parts = attr.split("__")
            obj: Any = doc
            for part in parts[1:]:  # skip leading "document"
                if obj is None:
                    return None
                obj = getattr(obj, part, None)
            return obj

        result: dict[str, FieldChange] = {}
        label_map = {
            "title": "title",
            "document_type__name": "document_type",
            "correspondent__name": "correspondent",
            "storage_path__name": "storage_path",
            "owner__username": "owner",
            "retention_until": "retention_until",
            "status": "status",
        }
        for attr, label in label_map.items():
            old_val = _val(doc_from, f"document__{attr}")
            new_val = _val(doc_to, f"document__{attr}")
            if old_val != new_val:
                result[label] = FieldChange(
                    old=str(old_val) if old_val is not None else None,
                    new=str(new_val) if new_val is not None else None,
                )
        return result

    @staticmethod
    def _tags_diff(v_from: DocumentVersion, v_to: DocumentVersion) -> TagDiff:
        """Ergibt hinzugefügte / entfernte Tags (alphabetisch sortiert)."""
        tags_from = {t.name for t in v_from.document.tags.all()}
        tags_to = {t.name for t in v_to.document.tags.all()}
        return TagDiff(
            added=sorted(tags_to - tags_from),
            removed=sorted(tags_from - tags_to),
        )

    @staticmethod
    def _custom_fields_diff(
        v_from: DocumentVersion, v_to: DocumentVersion
    ) -> dict[str, FieldChange]:
        """Vergleicht CustomFieldValues; nur Änderungen."""

        def _as_dict(doc: Document) -> dict[str, str]:
            return {
                cfv.field.name: cfv.value
                for cfv in doc.custom_field_values.all()
            }

        old_map = _as_dict(v_from.document)
        new_map = _as_dict(v_to.document)

        all_keys = set(old_map) | set(new_map)
        result: dict[str, FieldChange] = {}
        for key in sorted(all_keys):
            old_val = old_map.get(key)
            new_val = new_map.get(key)
            if old_val != new_val:
                result[key] = FieldChange(old=old_val, new=new_val)
        return result

    @staticmethod
    def _file_diff(v_from: DocumentVersion, v_to: DocumentVersion) -> FileDiff:
        """Vergleicht SHA256, Größe, MIME und Seitenanzahl."""
        sha_changed = v_from.sha256 != v_to.sha256
        size_changed = v_from.size != v_to.size
        mime_changed = v_from.mime_type != v_to.mime_type

        old_pages = v_from.page_count
        new_pages = v_to.page_count
        pages_changed = (
            old_pages is not None
            and new_pages is not None
            and old_pages != new_pages
        )

        return FileDiff(
            old_sha256=v_from.sha256,
            new_sha256=v_to.sha256,
            old_size=v_from.size,
            new_size=v_to.size,
            old_mime=v_from.mime_type,
            new_mime=v_to.mime_type,
            changed=sha_changed or size_changed or mime_changed,
            old_page_count=old_pages,
            new_page_count=new_pages,
            pages_changed=pages_changed,
        )
