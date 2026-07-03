"""Hilfsfunktionen für Audit-Einträge im Kontext Revisionssicherheit."""
from __future__ import annotations


def log_immutable_block(object_type: str, object_id) -> None:
    from .models import AuditLogEntry

    AuditLogEntry.objects.create(
        actor=None,
        action="immutable_block",
        object_type=object_type,
        object_id=str(object_id),
        detail={"reason": "WORM-Schutz: Schreib-/Löschversuch auf unveränderlicher Version"},
    )


def log_retention_block(object_type: str, object_id, retention_until) -> None:
    from .models import AuditLogEntry

    AuditLogEntry.objects.create(
        actor=None,
        action="retention_block",
        object_type=object_type,
        object_id=str(object_id),
        detail={"retention_until": str(retention_until), "reason": "Aufbewahrungsfrist aktiv"},
    )
