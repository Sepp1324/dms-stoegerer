"""Metadaten-Snapshot beim Sealing – Versionsvergleich Stufe 2 (STOAA-312).

Option A (freigegeben in STOAA-292, Owner-Signoff ``33669457``): beim Versiegeln
einer :class:`~documents.models.DocumentVersion` wird der aktuelle Stand der
Dokument-Metadaten, Tags und Custom-Fields deterministisch als JSON auf die
Version geschrieben. Der Snapshot ist **write-once** (die versiegelte Version ist
WORM – kein Update-Pfad) und fließt **kanonisch in die sha256-Siegelkette** der
Version ein (``seal_hash``): eine nachträgliche Manipulation der eingefrorenen
Metadaten bricht das Siegel und wird durch :func:`verify_seal` erkennbar.

Der gesamte Snapshot-/Siegel-Code lebt hier; Models/Pipeline rufen nur
:func:`write_snapshot_on_seal` bzw. :func:`verify_seal` auf. Determinismus:
``json.dumps(sort_keys=True, ensure_ascii=False)`` – reproduzierbare Bytes,
unabhängig von Dict-Reihenfolge.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Dict

from django.utils import timezone

if TYPE_CHECKING:  # pragma: no cover - nur für Typannotationen
    from documents.models import Document, DocumentVersion

SNAPSHOT_SCHEMA_VERSION = 1


def build_snapshot_payload(document: "Document", *, taken_at) -> Dict[str, Any]:
    """Baut den deterministischen Snapshot-Dict aus dem *heutigen* Dokument-Stand.

    Enthält Metadaten (title, document_type, correspondent, storage_path, owner,
    status, retention), die sortierte Tag-Liste (id+name) und die Custom-Fields
    ({field_name: value}). ``snapshot_taken_at`` ist Teil des Snapshots, damit der
    Erfassungszeitpunkt mit versiegelt (und damit unveränderlich) ist.
    """
    owner = document.owner
    metadata = {
        "title": document.title,
        "document_type": document.document_type.name if document.document_type else None,
        "correspondent": document.correspondent.name if document.correspondent else None,
        "storage_path": document.storage_path.name if document.storage_path else None,
        "owner": owner.username if owner else None,
        "status": document.status,
        "retention_until": (
            document.retention_until.isoformat() if document.retention_until else None
        ),
    }
    tags = sorted(
        ({"id": tag.id, "name": tag.name} for tag in document.tags.all()),
        key=lambda item: item["id"],
    )
    custom_fields = {
        cfv.field.name: cfv.value
        for cfv in document.custom_field_values.select_related("field").all()
    }
    return {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_taken_at": taken_at.isoformat() if taken_at else None,
        "metadata": metadata,
        "tags": tags,
        "custom_fields": custom_fields,
    }


def canonical_bytes(snapshot: Dict[str, Any]) -> bytes:
    """Kanonische, reproduzierbare Byte-Repräsentation des Snapshots."""
    return json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode("utf-8")


def compute_seal_hash(*, file_sha256: str, prev_hash: str, snapshot: Dict[str, Any]) -> str:
    """Bindet Datei-Hash, prev_hash und Snapshot-Bytes zum Metadaten-Siegel.

    Die drei Bestandteile werden mit einem ``0x00``-Trenner verkettet (der in
    hexadezimalen Hashes nicht vorkommt → keine Kollision durch Aneinanderreihung)
    und einmal ge-sha256't. Reproduzierbar aus dem gespeicherten Snapshot.
    """
    hasher = hashlib.sha256()
    hasher.update((file_sha256 or "").encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update((prev_hash or "").encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(canonical_bytes(snapshot))
    return hasher.hexdigest()


def write_snapshot_on_seal(version: "DocumentVersion", *, taken_at=None, actor=None) -> bool:
    """Schreibt den Snapshot **einmalig** auf die Version (write-once).

    Idempotent: existiert bereits ein Snapshot, passiert nichts (Rückgabe
    ``False``). Der Schreibvorgang läuft bewusst über ``QuerySet.update`` – die
    Version kann zum Aufrufzeitpunkt bereits WORM sein und der ``save()``-Guard
    würde greifen. ``seal_hash`` bindet den Snapshot an die Hash-Kette.

    Returns:
        ``True``, wenn ein Snapshot geschrieben wurde, sonst ``False`` (bereits
        vorhanden → kein Doppelschreiben).
    """
    from documents.models import AuditLogEntry, DocumentVersion

    if version.metadata_snapshot is not None:
        return False

    taken_at = taken_at or timezone.now()
    snapshot = build_snapshot_payload(version.document, taken_at=taken_at)
    seal_hash = compute_seal_hash(
        file_sha256=version.sha256,
        prev_hash=version.prev_hash,
        snapshot=snapshot,
    )

    DocumentVersion.objects.filter(pk=version.pk).update(
        metadata_snapshot=snapshot,
        snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_taken_at=taken_at,
        seal_hash=seal_hash,
    )
    version.metadata_snapshot = snapshot
    version.snapshot_schema_version = SNAPSHOT_SCHEMA_VERSION
    version.snapshot_taken_at = taken_at
    version.seal_hash = seal_hash

    AuditLogEntry.objects.create(
        actor=actor,
        action="metadata_snapshot",
        object_type="DocumentVersion",
        object_id=str(version.id),
        detail={
            "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
            "seal_hash": seal_hash,
            "taken_at": taken_at.isoformat(),
        },
    )
    return True


def verify_seal(version: "DocumentVersion") -> bool:
    """Prüft, ob der gespeicherte Snapshot noch zum ``seal_hash`` passt.

    Versionen ohne Snapshot (Stufe-1-Bestand) sind per Definition unverdächtig →
    ``True``. Für Versionen mit Snapshot wird der ``seal_hash`` aus dem aktuell
    gespeicherten Snapshot neu berechnet und mit dem versiegelten Wert verglichen –
    ein manipuliertes Metadatum bricht die Übereinstimmung.
    """
    if version.metadata_snapshot is None or not version.seal_hash:
        return True
    recomputed = compute_seal_hash(
        file_sha256=version.sha256,
        prev_hash=version.prev_hash,
        snapshot=version.metadata_snapshot,
    )
    return recomputed == version.seal_hash
