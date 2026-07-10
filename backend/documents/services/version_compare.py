"""Versionsvergleich Stufe 1 – reiner Vergleichs-Service (STOAA-289).

Die *gesamte* Vergleichslogik lebt hier; Models/ViewSets/Serializer bleiben
frei davon. Der Service ist isoliert testbar: :func:`compare_versions` nimmt zwei
:class:`~documents.models.DocumentVersion`-Instanzen plus das zugehörige
:class:`~documents.models.Document` entgegen – **nie** das Request-Objekt.

Stufe 1 arbeitet ausschließlich auf bereits versionierten ``DocumentVersion``-
Feldern (``ocr_text``, ``sha256``, ``size``, ``mime_type``, ``page_count``).
Es gibt daher **keine Migration**. ``metadata``/``tags``/``custom_fields`` sind
in Stufe 1 nicht versioniert und werden bewusst leer/``false`` zurückgegeben
(siehe Machbarkeits-Befund in STOAA-288); der ``metadata_versioning_supported``-
Flag signalisiert das dem Frontend, ohne die Antwort-Shape zu ändern.

Die Rückgabe ist so strukturiert, dass ein späterer visueller Seiten-Diff
(Stufe 2) rein *additiv* andockt – bestehende Felder bleiben stabil.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:  # pragma: no cover - nur für Typannotationen
    from documents.models import Document, DocumentVersion

PDF_MIME = "application/pdf"

FIELD_LABELS = {
    "title": "Titel",
    "created_at": "Belegdatum",
    "document_type": "Typ",
    "correspondent": "Korrespondent",
    "storage_path": "Ablagepfad",
    "folder": "Ordner",
    "case_file": "Akte",
    "owner": "Eigentümer",
    "status": "Freigabestatus",
    "review_status": "Review-Status",
    "retention_until": "Aufbewahrung bis",
}


@dataclass(frozen=True)
class CompareSummary:
    """Kompakte Änderungs-Flags über alle Vergleichsdimensionen.

    ``text_changed``/``binary_changed``/``pages_changed`` werden real berechnet.
    ``metadata_changed``/``tags_changed``/``custom_fields_changed`` sind in
    Stufe 1 fix ``False`` (diese Sektionen sind noch nicht versioniert).
    """

    text_changed: bool
    binary_changed: bool
    pages_changed: bool
    metadata_changed: bool = False
    tags_changed: bool = False
    custom_fields_changed: bool = False


@dataclass(frozen=True)
class FileComparison:
    """Datei-Ebene: Hash-, Größen-, MIME- und (bei PDF) Seiten-Vergleich.

    ``changed`` entspricht ``summary.binary_changed`` (287-Feldname beibehalten);
    die ``size``/``mime``/``page``-Felder sind additiv. ``both_pdf``/``pages_changed``
    tragen die PDF-Stufe (nur Architektur – keine Bildverarbeitung in Stufe 1).
    """

    old_sha256: str
    new_sha256: str
    old_size: int
    new_size: int
    old_mime_type: str
    new_mime_type: str
    old_page_count: int | None
    new_page_count: int | None
    changed: bool
    both_pdf: bool
    sha256_changed: bool
    size_delta: int
    mime_changed: bool


@dataclass(frozen=True)
class VersionComparison:
    """Vollständiges Vergleichsergebnis für zwei Versionen eines Dokuments."""

    document: int
    from_version: int
    to_version: int
    summary: CompareSummary
    text_diff: str
    text_diff_html: str
    files: FileComparison
    change_score: int = 0
    sections_changed: List[str] = field(default_factory=list)
    human_summary: List[str] = field(default_factory=list)
    page_summary: Dict[str, Any] = field(default_factory=dict)
    metadata_versioning_supported: bool = False
    # Stufe-1: vorhanden-aber-leer; Shape bleibt über Stufe 2 stabil.
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: Dict[str, List[Any]] = field(
        default_factory=lambda: {"added": [], "removed": []}
    )
    custom_fields: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialisiert in die stabile API-Shape (exakt an STOAA-287 orientiert)."""
        return {
            "document": self.document,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "summary": {
                "text_changed": self.summary.text_changed,
                "binary_changed": self.summary.binary_changed,
                "pages_changed": self.summary.pages_changed,
                "metadata_changed": self.summary.metadata_changed,
                "tags_changed": self.summary.tags_changed,
                "custom_fields_changed": self.summary.custom_fields_changed,
            },
            "text_diff": self.text_diff,
            "text_diff_html": self.text_diff_html,
            "metadata": self.metadata,
            "tags": self.tags,
            "custom_fields": self.custom_fields,
            "files": {
                "old_sha256": self.files.old_sha256,
                "new_sha256": self.files.new_sha256,
                "old_size": self.files.old_size,
                "new_size": self.files.new_size,
                "old_mime_type": self.files.old_mime_type,
                "new_mime_type": self.files.new_mime_type,
                "old_page_count": self.files.old_page_count,
                "new_page_count": self.files.new_page_count,
                "changed": self.files.changed,
                "both_pdf": self.files.both_pdf,
                "sha256_changed": self.files.sha256_changed,
                "size_delta": self.files.size_delta,
                "mime_changed": self.files.mime_changed,
            },
            "change_score": self.change_score,
            "sections_changed": self.sections_changed,
            "human_summary": self.human_summary,
            "page_summary": self.page_summary,
            "metadata_versioning_supported": self.metadata_versioning_supported,
        }


def _split_lines(text: str) -> List[str]:
    """Zerlegt OCR-Text zeilenweise für difflib.

    ``keepends=False`` + explizite ``lineterm``-Steuerung in den difflib-Aufrufen
    hält den Plaintext-Diff sauber. Leerer Text ergibt eine leere Zeilenliste –
    difflib kommt damit klar (kein Crash), der Diff zeigt dann reine Zufügungen
    bzw. Löschungen.
    """
    return (text or "").splitlines()


def _build_text_diff(from_text: str, to_text: str) -> str:
    """Unified-Diff als Plaintext-String (für die API / Copy-Paste)."""
    diff_lines = difflib.unified_diff(
        _split_lines(from_text),
        _split_lines(to_text),
        fromfile="from",
        tofile="to",
        lineterm="",
    )
    return "\n".join(diff_lines)


def _build_text_diff_html(from_text: str, to_text: str) -> str:
    """Side-by-Side-HTML-Tabelle (fürs Frontend)."""
    return difflib.HtmlDiff().make_table(
        _split_lines(from_text),
        _split_lines(to_text),
        fromdesc="from",
        todesc="to",
        context=True,
        numlines=3,
    )


def _compare_files(
    from_version: "DocumentVersion", to_version: "DocumentVersion"
) -> FileComparison:
    """Datei-Vergleich inkl. PDF-Stufe (Architektur-only)."""
    binary_changed = from_version.sha256 != to_version.sha256
    both_pdf = (
        from_version.mime_type == PDF_MIME and to_version.mime_type == PDF_MIME
    )
    return FileComparison(
        old_sha256=from_version.sha256,
        new_sha256=to_version.sha256,
        old_size=from_version.size,
        new_size=to_version.size,
        old_mime_type=from_version.mime_type,
        new_mime_type=to_version.mime_type,
        old_page_count=from_version.page_count,
        new_page_count=to_version.page_count,
        changed=binary_changed,
        both_pdf=both_pdf,
        sha256_changed=binary_changed,
        size_delta=to_version.size - from_version.size,
        mime_changed=from_version.mime_type != to_version.mime_type,
    )


def _pages_changed(files: FileComparison) -> bool:
    """Seitenzahl-Änderung – nur relevant, wenn beide Seiten PDF sind.

    Bei nicht-PDF bleibt ``pages_changed`` fix ``False`` (keine sinnvolle
    Seiten-Semantik). ``page_count`` kann ``None`` sein (noch nicht ermittelt) –
    ``!=`` behandelt ``None`` sauber als eigenen Wert.
    """
    if not files.both_pdf:
        return False
    return files.old_page_count != files.new_page_count


def _page_summary(files: FileComparison) -> Dict[str, Any]:
    """Leichte PDF-Seiten-Zusammenfassung ohne teure Pixelanalyse."""
    old_count = files.old_page_count
    new_count = files.new_page_count
    page_count_changed = _pages_changed(files)
    added = 0
    removed = 0
    if files.both_pdf and old_count is not None and new_count is not None:
        added = max(new_count - old_count, 0)
        removed = max(old_count - new_count, 0)
    return {
        "old_page_count": old_count,
        "new_page_count": new_count,
        "page_count_changed": page_count_changed,
        "added": added,
        "removed": removed,
        # Stufe 1 kennt keine stabile Seitenidentität pro Version. Diese Flags
        # bleiben bewusst false, bis eine echte Seitenmanifest-/Pixel-Diff-Stufe
        # sie belastbar berechnen kann.
        "reordered": False,
        "rotation_changed": False,
    }


# --- Snapshot-Diff (Stufe 2, STOAA-312) --------------------------------------
# Leere Sektionen exakt wie Stufe 1 – die Shape bleibt stabil, wenn (mindestens)
# eine Version keinen Snapshot hat (``metadata_versioning_supported == False``).
_EMPTY_METADATA: Dict[str, Any] = {}
_EMPTY_TAGS: Dict[str, List[Any]] = {"added": [], "removed": []}
_EMPTY_CUSTOM_FIELDS: Dict[str, Any] = {}


def _diff_dict(old: Dict[str, Any] | None, new: Dict[str, Any] | None) -> Dict[str, Any]:
    """Flacher Dict-Vergleich → ``{added, removed, changed}``.

    ``added``/``removed`` tragen die Werte der neu- bzw. weggefallenen Schlüssel,
    ``changed`` je Schlüssel ``{"old": …, "new": …}``. Deterministisch (sortierte
    Schlüssel).
    """
    old = old or {}
    new = new or {}
    added = {key: new[key] for key in sorted(new) if key not in old}
    removed = {key: old[key] for key in sorted(old) if key not in new}
    changed = {
        key: {"old": old[key], "new": new[key]}
        for key in sorted(old.keys() & new.keys())
        if old[key] != new[key]
    }
    return {"added": added, "removed": removed, "changed": changed}


def _diff_tags(
    old_tags: List[Dict[str, Any]] | None, new_tags: List[Dict[str, Any]] | None
) -> Dict[str, List[Any]]:
    """Tag-Vergleich über die Tag-``id`` → ``{added, removed}`` (id+name Objekte)."""
    old_by_id = {tag["id"]: tag for tag in (old_tags or [])}
    new_by_id = {tag["id"]: tag for tag in (new_tags or [])}
    added = [new_by_id[i] for i in sorted(new_by_id) if i not in old_by_id]
    removed = [old_by_id[i] for i in sorted(old_by_id) if i not in new_by_id]
    return {"added": added, "removed": removed}


def _diff_snapshots(
    from_version: "DocumentVersion", to_version: "DocumentVersion"
) -> Dict[str, Any]:
    """Diff der Metadaten/Tags/Custom-Fields aus BEIDEN Snapshots.

    ``metadata_versioning_supported`` ist nur ``True``, wenn beide verglichenen
    Versionen einen Snapshot tragen; sonst werden die (Stufe-1-)Leersektionen
    zurückgegeben und die Flags bleiben ``False`` – die Stufe-1-UX bleibt exakt
    unverändert.
    """
    old_snap = from_version.metadata_snapshot
    new_snap = to_version.metadata_snapshot
    supported = old_snap is not None and new_snap is not None
    if not supported:
        return {
            "supported": False,
            "metadata": dict(_EMPTY_METADATA),
            "tags": {"added": [], "removed": []},
            "custom_fields": dict(_EMPTY_CUSTOM_FIELDS),
            "metadata_changed": False,
            "tags_changed": False,
            "custom_fields_changed": False,
        }

    metadata = _diff_dict(old_snap.get("metadata"), new_snap.get("metadata"))
    custom_fields = _diff_dict(old_snap.get("custom_fields"), new_snap.get("custom_fields"))
    tags = _diff_tags(old_snap.get("tags"), new_snap.get("tags"))
    return {
        "supported": True,
        "metadata": metadata,
        "tags": tags,
        "custom_fields": custom_fields,
        "metadata_changed": any(metadata[section] for section in metadata),
        "tags_changed": bool(tags["added"] or tags["removed"]),
        "custom_fields_changed": any(custom_fields[section] for section in custom_fields),
    }


def _sections_changed(summary: CompareSummary) -> List[str]:
    sections: List[str] = []
    if summary.text_changed:
        sections.append("text")
    if summary.binary_changed:
        sections.append("file")
    if summary.pages_changed:
        sections.append("pages")
    if summary.metadata_changed:
        sections.append("metadata")
    if summary.tags_changed:
        sections.append("tags")
    if summary.custom_fields_changed:
        sections.append("custom_fields")
    return sections


def _change_score(summary: CompareSummary) -> int:
    score = 0
    if summary.text_changed:
        score += 30
    if summary.binary_changed:
        score += 20
    if summary.pages_changed:
        score += 15
    if summary.metadata_changed:
        score += 15
    if summary.tags_changed:
        score += 10
    if summary.custom_fields_changed:
        score += 10
    return min(score, 100)


def _human_summary(
    *,
    summary: CompareSummary,
    files: FileComparison,
    metadata: Dict[str, Any],
    tags: Dict[str, List[Any]],
    custom_fields: Dict[str, Any],
) -> List[str]:
    lines: List[str] = []
    if summary.text_changed:
        lines.append("Der erkannte OCR-/Dokumenttext hat sich geändert.")
    if files.sha256_changed:
        lines.append("Die Datei-Bytes unterscheiden sich (SHA-256 geändert).")
    if files.mime_changed:
        lines.append(
            f"Der Dateityp wechselte von {files.old_mime_type or 'unbekannt'} "
            f"zu {files.new_mime_type or 'unbekannt'}."
        )
    if files.size_delta:
        direction = "größer" if files.size_delta > 0 else "kleiner"
        lines.append(f"Die Datei wurde um {abs(files.size_delta)} Byte {direction}.")
    if summary.pages_changed:
        lines.append(
            f"Die Seitenzahl änderte sich von {files.old_page_count} "
            f"auf {files.new_page_count}."
        )
    if summary.metadata_changed:
        labels = _changed_labels(metadata)
        lines.append(
            "Metadaten geändert"
            + (": " + ", ".join(labels) if labels else ".")
        )
    if summary.tags_changed:
        added = ", ".join(tag.get("name", str(tag.get("id"))) for tag in tags["added"])
        removed = ", ".join(tag.get("name", str(tag.get("id"))) for tag in tags["removed"])
        parts = []
        if added:
            parts.append(f"hinzu: {added}")
        if removed:
            parts.append(f"entfernt: {removed}")
        lines.append("Schlagworte geändert" + (": " + "; ".join(parts) if parts else "."))
    if summary.custom_fields_changed:
        labels = _changed_labels(custom_fields)
        lines.append(
            "Zusatzfelder geändert"
            + (": " + ", ".join(labels) if labels else ".")
        )
    if not lines:
        lines.append("Keine relevanten Änderungen erkannt.")
    return lines


def _changed_labels(diff: Dict[str, Any]) -> List[str]:
    keys = set(diff.get("added", {}).keys())
    keys.update(diff.get("removed", {}).keys())
    keys.update(diff.get("changed", {}).keys())
    return [_field_label(key) for key in sorted(keys)]


def _field_label(key: str) -> str:
    return FIELD_LABELS.get(key, key)


def compare_versions(
    document: "Document",
    from_version: "DocumentVersion",
    to_version: "DocumentVersion",
) -> VersionComparison:
    """Vergleicht zwei Versionen eines Dokuments (Stufe 1: OCR/Datei/PDF).

    Args:
        document: Das gemeinsame Dokument beider Versionen (liefert die ID).
        from_version: Die *alte* Version (``version_no``).
        to_version: Die *neue* Version (``version_no``).

    Returns:
        Ein :class:`VersionComparison` mit real berechneten Text-/Datei-/PDF-
        Flags. Tragen BEIDE Versionen einen Metadaten-Snapshot (Stufe 2,
        STOAA-312), werden ``metadata``/``tags``/``custom_fields`` aus den
        Snapshots gediffed und ``metadata_versioning_supported`` ist ``True``;
        sonst bleiben diese Sektionen leer und das Flag ``False`` (Stufe-1-UX).

    Der Service ist seiteneffektfrei und berührt keine Request-/Permission-
    Ebene – Sichtbarkeit/Owner werden vom aufrufenden ViewSet erzwungen.

    # TODO Stufe 2: visueller Seiten-Diff (PDF-Seiten rendern und bildlich
    # vergleichen). Dockt additiv an ``FileComparison``/``VersionComparison`` an,
    # ohne die bestehende Shape zu verändern.
    """
    from_text = from_version.ocr_text or ""
    to_text = to_version.ocr_text or ""
    text_changed = from_text != to_text

    files = _compare_files(from_version, to_version)
    snap = _diff_snapshots(from_version, to_version)

    summary = CompareSummary(
        text_changed=text_changed,
        binary_changed=files.changed,
        pages_changed=_pages_changed(files),
        metadata_changed=snap["metadata_changed"],
        tags_changed=snap["tags_changed"],
        custom_fields_changed=snap["custom_fields_changed"],
    )
    page_summary = _page_summary(files)
    sections_changed = _sections_changed(summary)
    human_summary = _human_summary(
        summary=summary,
        files=files,
        metadata=snap["metadata"],
        tags=snap["tags"],
        custom_fields=snap["custom_fields"],
    )

    # ``text_diff_html`` bleibt bei Gleichheit leer (Spec-Item 2 aus STOAA-288);
    # nur bei tatsächlicher Textänderung wird die HtmlDiff-Tabelle erzeugt. Das FE
    # sanitized die Ausgabe vor dem Rendern.
    text_diff_html = _build_text_diff_html(from_text, to_text) if text_changed else ""

    return VersionComparison(
        document=document.id,
        from_version=from_version.version_no,
        to_version=to_version.version_no,
        summary=summary,
        text_diff=_build_text_diff(from_text, to_text),
        text_diff_html=text_diff_html,
        files=files,
        change_score=_change_score(summary),
        sections_changed=sections_changed,
        human_summary=human_summary,
        page_summary=page_summary,
        metadata_versioning_supported=snap["supported"],
        metadata=snap["metadata"],
        tags=snap["tags"],
        custom_fields=snap["custom_fields"],
    )
