"""Hilfsfunktionen für Audit-Einträge im Kontext Revisionssicherheit."""
from __future__ import annotations


def log_delete_block(
    object_type: str, object_id, *, action: str, reason: str, actor=None
) -> None:
    """Protokolliert eine Löschsperre mit dem KONKRETEN Blockiergrund (P1).

    ``action`` ist der jeweilige Grund (legal_hold_block/immutable_block/
    retention_block), NICHT pauschal immutable_block; siehe
    ``Document.delete_block``."""
    from .models import AuditLogEntry

    AuditLogEntry.objects.create(
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        detail={"reason": reason},
    )


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
