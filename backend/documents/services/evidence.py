"""Audit-/Beweis-Center für revisionsrelevante Dokumentnachweise.

Das Evidence Center verdichtet vorhandene Signale aus Archivprüfung, Versionen,
Hash-Kette, Metadatensiegeln, Retention und Audit-Log. Die Übersicht ist
absichtlich leichtgewichtig; die Detailansicht rechnet die Hash-Kette frisch.
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from documents import pipeline
from documents.models import AuditLogEntry, Document
from documents.services import archive as archive_service
from documents.services import version_snapshot
from documents.services.asn import format_asn

ISSUE_LIMIT = 50


def evidence_status(documents: QuerySet[Document] | Iterable[Document]) -> dict[str, Any]:
    """Liefert eine mandantengefilterte Beweisübersicht für die UI.

    Erwartet ein bereits owner-gescoptes Queryset aus der View-Schicht. So bleibt
    die gleiche Sichtbarkeitslogik wie bei Dokumentlisten/Detailseiten erhalten.
    """
    qs = _prepare_queryset(documents)
    summaries = [_document_summary(document) for document in qs]

    error_count = sum(1 for item in summaries if item["status"] == "error")
    warning_count = sum(1 for item in summaries if item["status"] == "warn")
    status = "error" if error_count else "warn" if warning_count else "ok"

    summary = {
        "documents": len(summaries),
        "evidence_ok": sum(1 for item in summaries if item["status"] == "ok"),
        "warnings": warning_count,
        "errors": error_count,
        "unchecked": sum(
            1
            for item in summaries
            if item["archive_status"] == Document.ArchiveStatus.UNCHECKED
        ),
        "archive_missing": sum(
            1
            for item in summaries
            if any(risk["code"] == "archive_missing" for risk in item["risks"])
        ),
        "hash_chain_errors": sum(
            1
            for item in summaries
            if any(risk["code"] == "hash_chain_error" for risk in item["risks"])
        ),
        "seal_missing": sum(
            1
            for item in summaries
            if any(risk["code"] == "seal_missing" for risk in item["risks"])
        ),
        "legal_hold": sum(1 for item in summaries if item["legal_hold"]),
        "retention_expired": sum(
            1 for item in summaries if item["retention"]["state"] == "expired"
        ),
    }

    issues = sorted(
        [item for item in summaries if item["status"] != "ok" or item["legal_hold"]],
        key=lambda item: (
            _severity_rank(item["status"]),
            item["score"],
            item["title"].lower(),
        ),
    )[:ISSUE_LIMIT]

    return {
        "status": status,
        "generated_at": timezone.now().isoformat(),
        "summary": summary,
        "issues": issues,
    }


def document_report(document: Document) -> dict[str, Any]:
    """Erzeugt einen frisch geprüften Nachweisbericht für ein einzelnes Dokument."""
    document = (
        Document.objects.select_related(
            "current_version",
            "document_type",
            "correspondent",
            "storage_path",
            "folder",
            "case_file",
            "owner",
        )
        .prefetch_related("versions", "tags")
        .get(pk=document.pk)
    )
    # Die teure Original-Hash-Kette GENAU EINMAL berechnen und an beide
    # Auswerter weiterreichen (P2): frueher hashte _document_summary sie und
    # verify_document_archive gleich danach ERNEUT (auf NFS doppelte I/O/Latenz).
    # Ebenso wird das aktuelle Archiv-PDF nur noch einmal gehasht (archive_files).
    integrity = pipeline.verify_document_integrity(document)
    archive_report = archive_service.verify_document_archive(
        document, persist=False, integrity=integrity
    )
    summary = _document_summary(
        document,
        verify_hash=True,
        integrity=integrity,
        archive_files=archive_report.get("archive_files"),
    )
    versions = [
        _version_report(version)
        for version in document.versions.order_by("version_no")
    ]
    audit = _audit_summary(document)

    return {
        **summary,
        "document_type": document.document_type.name if document.document_type else None,
        "correspondent": document.correspondent.name if document.correspondent else None,
        "storage_path": document.storage_path.name if document.storage_path else None,
        "folder": document.folder.full_path if document.folder else None,
        "case_file": document.case_file.title if document.case_file else None,
        "owner": document.owner.username if document.owner else None,
        "tags": [tag.name for tag in document.tags.order_by("name", "id")],
        "versions": versions,
        "audit": audit,
        "archive_report": archive_report,
    }


def _prepare_queryset(documents: QuerySet[Document] | Iterable[Document]):
    if isinstance(documents, QuerySet):
        return documents.select_related(
            "current_version",
            "document_type",
            "correspondent",
            "storage_path",
            "folder",
            "case_file",
            "owner",
        ).prefetch_related("versions", "tags")
    return documents


def _document_summary(
    document: Document,
    *,
    verify_hash: bool = False,
    integrity: dict | None = None,
    archive_files: list[dict] | None = None,
) -> dict[str, Any]:
    current = document.current_version
    risks: list[dict[str, str]] = []
    checks: list[dict[str, Any]] = []
    archive_report = document.archive_report or {}

    retention = archive_service.retention_state(document)
    if document.archive_status == Document.ArchiveStatus.ERROR:
        _risk(
            risks,
            "archive_error",
            "error",
            document.archive_error or "Archivprüfung fehlgeschlagen.",
        )
    elif document.archive_status == Document.ArchiveStatus.WARNING:
        _risk(
            risks,
            "archive_warning",
            "warn",
            document.archive_error or "Archivprüfung hat Warnungen.",
        )
    elif document.archive_status == Document.ArchiveStatus.UNCHECKED:
        _risk(risks, "archive_unchecked", "warn", "Archivprüfung wurde noch nicht ausgeführt.")

    if archive_report.get("integrity", {}).get("chain_ok") is False:
        _risk(
            risks,
            "hash_chain_error",
            "error",
            "Datei-Hash oder Versionskette ist fehlerhaft.",
        )

    if retention["state"] == "expired":
        _risk(risks, "retention_expired", "warn", "Aufbewahrungsfrist ist abgelaufen.")
    elif retention["state"] == "due_soon":
        _risk(risks, "retention_due_soon", "warn", "Aufbewahrungsfrist läuft bald ab.")

    if current is None:
        _risk(risks, "version_missing", "error", "Keine aktuelle Version vorhanden.")
        checks.append(_check("current_version", "error", "Aktuelle Version fehlt."))
    else:
        checks.extend(
            _current_version_checks(
                current, risks, verify_hash=verify_hash, archive_files=archive_files
            )
        )

    if verify_hash and integrity is None:
        # Nur berechnen, wenn nicht schon vom Aufrufer (document_report) geteilt.
        integrity = pipeline.verify_document_integrity(document)
    if verify_hash:
        checks.append(
            _check(
                "hash_chain",
                "ok" if integrity.get("chain_ok") else "error",
                "Hash-Kette frisch verifiziert."
                if integrity.get("chain_ok")
                else "Hash-Kette ist fehlerhaft.",
            )
        )
        if not integrity.get("chain_ok"):
            _risk(
                risks,
                "hash_chain_error",
                "error",
                "Datei-Hash oder Versionskette ist fehlerhaft.",
            )
    elif document.archive_status == Document.ArchiveStatus.UNCHECKED:
        _risk(risks, "hash_chain_unchecked", "warn", "Hash-Kette wurde noch nicht archivseitig geprüft.")

    score = _score(risks)
    status = _status(risks)

    return {
        "document_id": document.id,
        "title": document.title,
        "asn": document.asn,
        "asn_label": format_asn(document.asn) if document.asn else None,
        "status": status,
        "score": score,
        "risks": risks,
        "checks": checks,
        "archive_status": document.archive_status,
        "archive_status_label": document.get_archive_status_display(),
        "archive_checked_at": document.archive_checked_at.isoformat()
        if document.archive_checked_at
        else None,
        "archive_error": document.archive_error,
        "retention": retention,
        "legal_hold": document.legal_hold,
        "legal_hold_reason": document.legal_hold_reason,
        "current_version": current.id if current else None,
        "processing_state": current.processing_state if current else None,
        "generated_at": timezone.now().isoformat(),
        "integrity": integrity,
    }


def _current_version_checks(
    version,
    risks: list[dict[str, str]],
    *,
    verify_hash: bool = False,
    archive_files: list[dict] | None = None,
) -> list[dict[str, Any]]:
    checks = []
    file_present = os.path.exists(version.file_path)
    checks.append(
        _check("original_file", "ok" if file_present else "error", version.file_path)
    )
    if not file_present:
        _risk(risks, "original_missing", "error", "Originaldatei fehlt auf dem Speicher.")

    archive_present = bool(version.archive_path and os.path.exists(version.archive_path))
    # Archiv-Integrität (P1/P2): Den vollen Archiv-SHA-256 NUR in der Detailansicht
    # (``verify_hash``) frisch berechnen – die Übersicht (evidence_status) würde
    # sonst pro Seitenaufruf JEDE Archivdatei komplett lesen (Timeout bei NFS/vielen
    # Dokumenten). Ein Lesefehler (PermissionError/OSError/NFS) darf das Center NICHT
    # mit 500 kippen – er IST der zu meldende Fehlerzustand.
    archive_note = ""
    archive_bad = False
    # Hat der Aufrufer (document_report) das Archiv bereits ueber
    # verify_document_archive geprueft, das strukturierte Ergebnis wiederverwenden
    # statt das Archiv-PDF ein ZWEITES Mal zu hashen (P2).
    shared = None
    if archive_files is not None:
        shared = next(
            (a for a in archive_files if a.get("version_no") == version.version_no),
            None,
        )
    if shared is not None:
        archive_bad = not shared.get("ok", True)
        if shared.get("note"):
            archive_note = f" ({shared['note']})"
    elif verify_hash and archive_present and version.archive_sha256:
        try:
            if pipeline.sha256_of(version.archive_path) != version.archive_sha256:
                archive_bad = True
                archive_note = " (Hash-Mismatch)"
        except OSError:
            archive_bad = True
            archive_note = " (nicht lesbar)"
    archive_state = "error" if archive_bad else ("ok" if archive_present else "warn")
    checks.append(
        _check("archive_file", archive_state, (version.archive_path or "") + archive_note)
    )
    if archive_bad:
        _risk(
            risks, "archive_tampered", "error",
            "Archiv-PDF verändert oder nicht lesbar (Hash stimmt nicht).",
        )
    elif not archive_present:
        _risk(risks, "archive_missing", "warn", "OCR-/Archiv-PDF fehlt.")

    thumbnail_present = bool(version.thumbnail_path and os.path.exists(version.thumbnail_path))
    checks.append(
        _check(
            "thumbnail",
            "ok" if thumbnail_present else "warn",
            version.thumbnail_path or "",
        )
    )
    if not thumbnail_present:
        _risk(risks, "thumbnail_missing", "warn", "Miniaturbild fehlt.")

    if version.processing_state != version.ProcessingState.READY:
        _risk(
            risks,
            "not_ready",
            "warn",
            f"Verarbeitung ist noch nicht READY ({version.processing_state}).",
        )
    checks.append(
        _check(
            "processing_state",
            "ok" if version.processing_state == version.ProcessingState.READY else "warn",
            version.processing_state,
        )
    )

    checks.append(
        _check(
            "worm",
            "ok" if version.is_immutable else "warn",
            "WORM versiegelt" if version.is_immutable else "Nicht versiegelt",
        )
    )
    if not version.is_immutable:
        _risk(risks, "worm_missing", "warn", "Aktuelle Version ist nicht WORM-versiegelt.")

    snapshot_present = (
        version.metadata_snapshot is not None and version.snapshot_schema_version > 0
    )
    checks.append(
        _check(
            "metadata_snapshot",
            "ok" if snapshot_present else "warn",
            f"Schema {version.snapshot_schema_version}",
        )
    )
    if not snapshot_present:
        _risk(risks, "snapshot_missing", "warn", "Metadaten-Snapshot fehlt.")

    seal_ok = bool(version.seal_hash and version_snapshot.verify_seal(version))
    checks.append(_check("metadata_seal", "ok" if seal_ok else "error", version.seal_hash))
    if not seal_ok:
        _risk(risks, "seal_missing", "error", "Metadaten-Siegel fehlt oder ist ungültig.")

    return checks


def _version_report(version) -> dict[str, Any]:
    return {
        "id": version.id,
        "version_no": version.version_no,
        "sha256": version.sha256,
        "prev_hash": version.prev_hash,
        "file_present": os.path.exists(version.file_path),
        "archive_present": bool(
            version.archive_path and os.path.exists(version.archive_path)
        ),
        "thumbnail_present": bool(
            version.thumbnail_path and os.path.exists(version.thumbnail_path)
        ),
        "processing_state": version.processing_state,
        "ocr_status": version.ocr_status,
        "page_count": version.page_count,
        "size": version.size,
        "is_immutable": version.is_immutable,
        "metadata_snapshot_present": version.metadata_snapshot is not None,
        "snapshot_schema_version": version.snapshot_schema_version,
        "snapshot_taken_at": version.snapshot_taken_at.isoformat()
        if version.snapshot_taken_at
        else None,
        "seal_hash": version.seal_hash,
        "seal_ok": bool(version.seal_hash and version_snapshot.verify_seal(version)),
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


def _audit_summary(document: Document) -> dict[str, Any]:
    version_ids = [str(item.id) for item in document.versions.all()]
    entries = (
        AuditLogEntry.objects.select_related("actor")
        .filter(object_type="Document", object_id=str(document.id))
        | AuditLogEntry.objects.select_related("actor").filter(
            object_type="DocumentVersion",
            object_id__in=version_ids,
        )
    ).order_by("-timestamp", "-id")
    latest = entries[:10]
    return {
        "count": entries.count(),
        "latest": [
            {
                "id": entry.id,
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
                "actor": entry.actor.username if entry.actor else None,
                "action": entry.action,
                "object_type": entry.object_type,
                "object_id": entry.object_id,
                "detail": entry.detail,
            }
            for entry in latest
        ],
    }


def _risk(risks: list[dict[str, str]], code: str, severity: str, message: str) -> None:
    if not any(item["code"] == code for item in risks):
        risks.append({"code": code, "severity": severity, "message": message})


def _check(code: str, status: str, detail: str) -> dict[str, str]:
    return {"code": code, "status": status, "detail": detail}


def _status(risks: list[dict[str, str]]) -> str:
    if any(item["severity"] == "error" for item in risks):
        return "error"
    if risks:
        return "warn"
    return "ok"


def _score(risks: list[dict[str, str]]) -> int:
    score = 100
    for risk in risks:
        score -= 25 if risk["severity"] == "error" else 10
    return max(0, score)


def _severity_rank(status: str) -> int:
    return {"error": 0, "warn": 1, "ok": 2}.get(status, 3)
