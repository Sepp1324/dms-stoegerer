"""Metadaten-Snapshot & Siegel-Verkettung (STOAA-315, Versionsvergleich Stufe 2).

Kapselt die *kanonische* Point-in-time-Abbildung des ``Document``-Zustands je
Version und die daraus abgeleitete ``seal_hash``-Siegelgröße. Beide Bausteine
sind bewusst hier isoliert (kein Django-View, kein Request), damit sie aus der
Pipeline (Sealing), dem Backfill-Command **und** dem Vergleichs-Service
(``version_compare``) identisch aufgerufen werden – eine einzige Quelle der
Kanonik, sonst bricht die Hash-Reproduzierbarkeit.

Kanonik-Vertrag (verbindlich, siehe STOAA-315):
  * Feste Schlüssel-Reihenfolge ist irrelevant, weil ``canonical_json`` mit
    ``sort_keys=True`` serialisiert – aber Listen (``tags``) werden VOR der
    Serialisierung sortiert, damit die Reihenfolge deterministisch ist.
  * Skalare werden als stabile Primitive gespeichert (Labels/Namen statt
    FK-IDs, ``None`` für „nicht gesetzt"), damit der spätere Diff lesbar und
    reproduzierbar bleibt.
  * ``json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",",":"))``
    ist die EINZIGE erlaubte Serialisierung für den Siegel-Hash.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:  # pragma: no cover - nur für Typannotationen
    from documents.models import Document

# Skalare Metadatenfelder des Snapshots (Reihenfolge = Diff-Reihenfolge im FE).
SCALAR_KEYS = (
    "title",
    "correspondent",
    "document_type",
    "storage_path",
    "owner",
    "status",
    "retention_until",
)


def canonical_json(data: Any) -> str:
    """Deterministische JSON-Serialisierung als Basis des Siegel-Hashes.

    ``sort_keys`` macht die Schlüsselreihenfolge irrelevant, ``ensure_ascii=False``
    hält Umlaute stabil (kein ``\\uXXXX``-Escaping, das sich zwischen Versionen
    unterscheiden könnte), ``separators`` entfernt Whitespace. Nur so ist der
    ``seal_hash`` über Zeit/Systeme reproduzierbar.
    """
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def build_metadata_snapshot(document: "Document") -> Dict[str, Any]:
    """Baut den kanonischen Point-in-time-Snapshot des ``Document``-Zustands.

    Werte sind stabile Primitive: Namen/Labels der FKs (``None`` wenn nicht
    gesetzt), sortierte Tag-Namen und ein ``{feldname: wert}``-Dict der
    Custom-Fields. Der Aufrufer entscheidet, wann der Snapshot eingefroren wird
    (Sealing = aktueller Zustand; Backfill = aktueller Zustand der current_version).
    """
    tags = sorted(tag.name for tag in document.tags.all())
    custom_fields = {
        cfv.field.name: cfv.value
        for cfv in document.custom_field_values.select_related("field").all()
    }
    retention = document.retention_until
    return {
        "title": document.title,
        "correspondent": document.correspondent.name if document.correspondent_id else None,
        "document_type": document.document_type.name if document.document_type_id else None,
        "storage_path": document.storage_path.name if document.storage_path_id else None,
        "owner": document.owner.username if document.owner_id else None,
        "status": document.status,
        "retention_until": retention.isoformat() if retention else None,
        "tags": tags,
        "custom_fields": custom_fields,
    }


def compute_seal_hash(version_sha256: str, prev_hash: str | None, snapshot: Any) -> str:
    """Berechnet die Siegelgröße, die Datei-Hash UND Metadaten-Snapshot bindet.

    ``sha256( version.sha256 + "|" + (prev_hash or "") + "|" + canonical_json(snapshot) )``

    Der Datei-Hash (``version.sha256``) wird NICHT umgewidmet – er bleibt die
    Grundlage von Dedup und Datei-Kette. ``seal_hash`` ist eine *zusätzliche*
    Größe, die über ``prev_hash`` transitiv verkettet ist: Manipulation an der
    Datei ODER an den Metadaten eines Snapshots wird beim Verify erkennbar.
    """
    payload = f"{version_sha256}|{prev_hash or ''}|{canonical_json(snapshot)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
