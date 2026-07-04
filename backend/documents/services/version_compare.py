"""Versionsvergleich – reiner Vergleichs-Service (STOAA-289 Stufe 1 + STOAA-315 Stufe 2).

Die *gesamte* Vergleichslogik lebt hier; Models/ViewSets/Serializer bleiben
frei davon. Der Service ist isoliert testbar: :func:`compare_versions` nimmt zwei
:class:`~documents.models.DocumentVersion`-Instanzen plus das zugehörige
:class:`~documents.models.Document` entgegen – **nie** das Request-Objekt.

Stufe 1 (STOAA-289) arbeitet auf bereits versionierten ``DocumentVersion``-
Feldern (``ocr_text``, ``sha256``, ``size``, ``mime_type``, ``page_count``).

Stufe 2 (STOAA-315) ergänzt den echten Metadaten-/Tag-/Custom-Field-Diff aus dem
beim Sealing eingefrorenen ``metadata_snapshot`` beider Versionen. Der Diff ist
**rein additiv**: Tragen BEIDE Versionen einen Snapshot, werden
``metadata``/``tags``/``custom_fields`` gefüllt und
``metadata_versioning_supported=True`` gesetzt; fehlt einer der Snapshots
(Altbestand vor dem Feature), bleibt exakt die Stufe-1-Shape (leer + ``False``)
erhalten – der ``metadata_versioning_supported``-Flag signalisiert das dem
Frontend „nicht verfügbar", ohne die Antwort-Shape zu brechen.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:  # pragma: no cover - nur für Typannotationen
    from documents.models import Document, DocumentVersion

PDF_MIME = "application/pdf"


@dataclass(frozen=True)
class CompareSummary:
    """Kompakte Änderungs-Flags über alle Vergleichsdimensionen.

    ``text_changed``/``binary_changed``/``pages_changed`` werden real berechnet.
    ``metadata_changed``/``tags_changed``/``custom_fields_changed`` sind real ab
    Stufe 2 (STOAA-315), wenn beide Versionen einen ``metadata_snapshot`` tragen;
    sonst bleiben sie – wie in Stufe 1 – ``False`` (Default).
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
    metadata_versioning_supported: bool = False
    # Default = Stufe-1-Shape (vorhanden-aber-leer). Ab Stufe 2 gefüllt, wenn
    # beide Snapshots vorliegen: ``metadata`` = {feld: {old,new}}, ``tags`` =
    # {added:[…], removed:[…]}, ``custom_fields`` = {added:{}, removed:{}, changed:{}}.
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
            },
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


# ---------------------------------------------------------------------------
# Stufe 2 (STOAA-315): echter Metadaten-/Tag-/Custom-Field-Diff aus den
# ``metadata_snapshot``-Feldern. Nur aktiv, wenn BEIDE verglichenen Versionen
# einen Snapshot tragen – sonst bleibt die Stufe-1-Shape (leer + false)
# unverändert (Altbestand → „nicht verfügbar", identisch zur Stufe-1-UX).
# ---------------------------------------------------------------------------
def _diff_scalar_metadata(
    old_snap: Dict[str, Any], new_snap: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    """Diff der skalaren Metadatenfelder (title/correspondent/… – ohne Listen).

    Rückgabe: ``{feld: {"old": <alt>, "new": <neu>}}`` nur für geänderte Felder.
    """
    from documents.services.metadata_snapshot import SCALAR_KEYS

    changed: Dict[str, Dict[str, Any]] = {}
    for key in SCALAR_KEYS:
        old_value = old_snap.get(key)
        new_value = new_snap.get(key)
        if old_value != new_value:
            changed[key] = {"old": old_value, "new": new_value}
    return changed


def _diff_tags(
    old_snap: Dict[str, Any], new_snap: Dict[str, Any]
) -> Dict[str, List[Any]]:
    """Tag-Diff über die (bereits sortierten) Namenslisten der Snapshots."""
    old_tags = set(old_snap.get("tags") or [])
    new_tags = set(new_snap.get("tags") or [])
    return {
        "added": sorted(new_tags - old_tags),
        "removed": sorted(old_tags - new_tags),
    }


def _diff_custom_fields(
    old_snap: Dict[str, Any], new_snap: Dict[str, Any]
) -> Dict[str, Any]:
    """Custom-Field-Diff: added/removed/changed über die ``{name: wert}``-Dicts."""
    old_cf = old_snap.get("custom_fields") or {}
    new_cf = new_snap.get("custom_fields") or {}
    added = {k: new_cf[k] for k in sorted(new_cf) if k not in old_cf}
    removed = {k: old_cf[k] for k in sorted(old_cf) if k not in new_cf}
    changed = {
        k: {"old": old_cf[k], "new": new_cf[k]}
        for k in sorted(old_cf)
        if k in new_cf and old_cf[k] != new_cf[k]
    }
    return {"added": added, "removed": removed, "changed": changed}


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
        Flags. ``metadata``/``tags``/``custom_fields`` werden **nur** gefüllt und
        ``metadata_versioning_supported=True`` gesetzt, wenn BEIDE Versionen
        einen ``metadata_snapshot`` tragen (Stufe 2, STOAA-315). Fehlt einer der
        Snapshots, bleibt die Stufe-1-Shape (leer + ``False``) unverändert –
        FE/QA aus Stufe 1 brechen nicht.

    Der Service ist seiteneffektfrei und berührt keine Request-/Permission-
    Ebene – Sichtbarkeit/Owner werden vom aufrufenden ViewSet erzwungen.

    # TODO Stufe 2 (visueller Seiten-Diff): PDF-Seiten rendern und bildlich
    # vergleichen. Dockt additiv an ``FileComparison``/``VersionComparison`` an,
    # ohne die bestehende Shape zu verändern.
    """
    from_text = from_version.ocr_text or ""
    to_text = to_version.ocr_text or ""
    text_changed = from_text != to_text

    files = _compare_files(from_version, to_version)

    # Metadaten-Diff (Stufe 2) nur, wenn BEIDE Snapshots vorhanden sind.
    old_snap = from_version.metadata_snapshot
    new_snap = to_version.metadata_snapshot
    metadata_supported = old_snap is not None and new_snap is not None

    if metadata_supported:
        metadata_diff = _diff_scalar_metadata(old_snap, new_snap)
        tags_diff = _diff_tags(old_snap, new_snap)
        custom_fields_diff = _diff_custom_fields(old_snap, new_snap)
        metadata_changed = bool(metadata_diff)
        tags_changed = bool(tags_diff["added"] or tags_diff["removed"])
        custom_fields_changed = bool(
            custom_fields_diff["added"]
            or custom_fields_diff["removed"]
            or custom_fields_diff["changed"]
        )
    else:
        # Stufe-1-Shape unverändert (leer + false).
        metadata_diff = {}
        tags_diff = {"added": [], "removed": []}
        custom_fields_diff = {}
        metadata_changed = tags_changed = custom_fields_changed = False

    summary = CompareSummary(
        text_changed=text_changed,
        binary_changed=files.changed,
        pages_changed=_pages_changed(files),
        metadata_changed=metadata_changed,
        tags_changed=tags_changed,
        custom_fields_changed=custom_fields_changed,
    )

    return VersionComparison(
        document=document.id,
        from_version=from_version.version_no,
        to_version=to_version.version_no,
        summary=summary,
        text_diff=_build_text_diff(from_text, to_text),
        text_diff_html=_build_text_diff_html(from_text, to_text),
        files=files,
        metadata_versioning_supported=metadata_supported,
        metadata=metadata_diff,
        tags=tags_diff,
        custom_fields=custom_fields_diff,
    )
