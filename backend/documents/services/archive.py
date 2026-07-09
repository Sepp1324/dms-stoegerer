"""Archiv-/Retention-Center.

Dieses Modul bündelt die revisionsrelevanten Prüfungen: Datei-Hash-Kette,
Metadaten-Siegel, WORM-Status, Retention und Legal Hold. Die View-Schicht fragt
nur noch den kompakten Status ab; die teuren Dateihashes laufen bewusst in
expliziten Prüfaktionen oder Management Commands.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from django.db.models import Count, Q
from django.utils import timezone

from documents import pipeline
from documents.models import Document
from documents.services import version_snapshot

RETENTION_DUE_SOON_DAYS = 90


def verify_document_archive(document: Document, *, persist: bool = True) -> dict:
    """Prüft ein Dokument vollständig und speichert optional die Archiv-Ampel."""
    integrity = pipeline.verify_document_integrity(document)
    versions = list(document.versions.order_by("version_no"))
    seal_results = [_seal_result(version) for version in versions]

    errors = []
    warnings = []
    if not versions:
        warnings.append("Dokument hat keine Versionen.")
    if not integrity["chain_ok"]:
        errors.append("Datei-Hash oder Versionskette ist fehlerhaft.")
    if any(not item["seal_ok"] for item in seal_results):
        errors.append("Mindestens ein Metadaten-Siegel ist ungültig.")

    current = document.current_version
    if current is None:
        warnings.append("Keine aktuelle Version gesetzt.")
    else:
        if current.processing_state != current.ProcessingState.READY:
            warnings.append(f"Aktuelle Version ist noch nicht READY ({current.processing_state}).")
        if not current.is_immutable:
            warnings.append("Aktuelle Version ist nicht WORM-versiegelt.")
        if current.metadata_snapshot is None:
            warnings.append("Aktuelle Version hat keinen Metadaten-Snapshot.")

    status = Document.ArchiveStatus.OK
    if errors:
        status = Document.ArchiveStatus.ERROR
    elif warnings:
        status = Document.ArchiveStatus.WARNING

    retention = retention_state(document)
    report = {
        "status": status,
        "checked_at": timezone.now().isoformat(),
        "integrity": integrity,
        "seals": seal_results,
        "retention": retention,
        "legal_hold": {
            "enabled": document.legal_hold,
            "reason": document.legal_hold_reason,
            "set_at": document.legal_hold_set_at.isoformat()
            if document.legal_hold_set_at
            else None,
        },
        "warnings": warnings,
        "errors": errors,
    }

    if persist:
        Document.objects.filter(pk=document.pk).update(
            archive_status=status,
            archive_checked_at=timezone.now(),
            archive_error="; ".join(errors or warnings),
            archive_report=report,
        )
        document.archive_status = status
        document.archive_error = "; ".join(errors or warnings)
        document.archive_report = report
    return report


def archive_health(documents: Iterable[Document] | None = None, *, issue_limit: int = 25) -> dict:
    """Liefert eine billige Systemübersicht aus den gespeicherten Archiv-Ampeln."""
    now = timezone.now()
    today = now.date()
    due_until = today + timedelta(days=RETENTION_DUE_SOON_DAYS)
    qs = documents if documents is not None else Document.objects.all()
    qs = qs.select_related(
        "current_version",
        "document_type",
        "correspondent",
        "legal_hold_set_by",
    )

    counts = dict(qs.values_list("archive_status").annotate(count=Count("id")))
    total = qs.count()
    legal_hold_count = qs.filter(legal_hold=True).count()
    retention_active = qs.filter(retention_until__gt=today).count()
    retention_due_soon = qs.filter(
        retention_until__gte=today,
        retention_until__lte=due_until,
    ).count()
    retention_expired = qs.filter(retention_until__lt=today).count()

    status = "ok"
    if counts.get(Document.ArchiveStatus.ERROR, 0):
        status = "error"
    elif counts.get(Document.ArchiveStatus.WARNING, 0) or counts.get(
        Document.ArchiveStatus.UNCHECKED, 0
    ):
        status = "warn"

    issue_qs = qs.filter(
        Q(archive_status__in=[Document.ArchiveStatus.ERROR, Document.ArchiveStatus.WARNING])
        | Q(legal_hold=True)
        | Q(retention_until__lte=due_until)
    ).order_by("archive_status", "retention_until", "-archive_checked_at", "-added_at")[
        :issue_limit
    ]

    return {
        "status": status,
        "generated_at": now.isoformat(),
        "thresholds": {"retention_due_soon_days": RETENTION_DUE_SOON_DAYS},
        "summary": {
            "documents": total,
            "archive_ok": counts.get(Document.ArchiveStatus.OK, 0),
            "archive_warning": counts.get(Document.ArchiveStatus.WARNING, 0),
            "archive_error": counts.get(Document.ArchiveStatus.ERROR, 0),
            "archive_unchecked": counts.get(Document.ArchiveStatus.UNCHECKED, 0),
            "legal_hold": legal_hold_count,
            "retention_active": retention_active,
            "retention_due_soon": retention_due_soon,
            "retention_expired": retention_expired,
        },
        "issues": [_archive_issue_row(document) for document in issue_qs],
    }


def retention_state(document: Document, *, today: date | None = None) -> dict:
    """Klassifiziert die Aufbewahrungssituation eines Dokuments."""
    today = today or timezone.now().date()
    until = document.retention_until
    if document.legal_hold:
        state = "legal_hold"
    elif until is None:
        state = "none"
    elif until < today:
        state = "expired"
    elif until <= today + timedelta(days=RETENTION_DUE_SOON_DAYS):
        state = "due_soon"
    else:
        state = "active"
    return {
        "state": state,
        "retention_until": until.isoformat() if until else None,
        "days_remaining": (until - today).days if until else None,
    }


def _seal_result(version) -> dict:
    snapshot_present = version.metadata_snapshot is not None
    seal_ok = version_snapshot.verify_seal(version)
    return {
        "version_id": version.id,
        "version_no": version.version_no,
        "snapshot_present": snapshot_present,
        "seal_hash": version.seal_hash,
        "seal_ok": seal_ok,
        "immutable": version.is_immutable,
    }


def _archive_issue_row(document: Document) -> dict:
    retention = retention_state(document)
    return {
        "document_id": document.id,
        "title": document.title,
        "asn": document.asn,
        "archive_status": document.archive_status,
        "archive_status_label": document.get_archive_status_display(),
        "archive_checked_at": document.archive_checked_at.isoformat()
        if document.archive_checked_at
        else None,
        "archive_error": document.archive_error,
        "retention": retention,
        "legal_hold": document.legal_hold,
        "legal_hold_reason": document.legal_hold_reason,
        "current_version": document.current_version_id,
    }
