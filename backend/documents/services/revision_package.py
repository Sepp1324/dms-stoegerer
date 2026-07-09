"""Revisionspaket-Export für einzelne Dokumente.

Das Paket ist bewusst selbsterklärend aufgebaut: Dateien liegen unter
``files/``, OCR-Text unter ``text/`` und alle prüfbaren Begleitdaten als JSON im
Root. ``manifest.json`` enthält SHA-256 und Größe jedes Paketbestandteils.
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from django.utils import timezone
from django.utils.text import slugify

from documents.models import AuditLogEntry, CaseFile, Document
from documents.services import archive as archive_service
from documents.services.asn import format_asn


@dataclass(frozen=True)
class RevisionPackage:
    filename: str
    content: bytes
    manifest: dict[str, Any]


def build_document_revision_package(document: Document) -> RevisionPackage:
    """Baut ein ZIP-Revisionspaket für ein sichtbares Dokument."""
    generated_at = timezone.now()
    archive_report = archive_service.verify_document_archive(document, persist=False)
    manifest_entries: list[dict[str, Any]] = []
    missing_files: list[dict[str, Any]] = []

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        _write_json(
            zf,
            "metadata.json",
            _metadata_payload(document, generated_at=generated_at),
            manifest_entries,
        )
        _write_json(
            zf,
            "integrity.json",
            archive_report,
            manifest_entries,
        )
        _write_json(
            zf,
            "retention.json",
            _retention_payload(document, archive_report),
            manifest_entries,
        )
        _write_json(
            zf,
            "audit.json",
            _audit_payload(document),
            manifest_entries,
        )

        for version in document.versions.order_by("version_no"):
            base = f"files/v{version.version_no}"
            original_name = f"{base}/original{_suffix(version.file_path)}"
            _write_file(zf, original_name, version.file_path, manifest_entries, missing_files)

            if version.archive_path and version.archive_path != version.file_path:
                _write_file(
                    zf,
                    f"{base}/archive{_suffix(version.archive_path, fallback='.pdf')}",
                    version.archive_path,
                    manifest_entries,
                    missing_files,
                )

            if version.ocr_text:
                _write_text(
                    zf,
                    f"text/v{version.version_no}-ocr.txt",
                    version.ocr_text,
                    manifest_entries,
                )

            if version.metadata_snapshot is not None:
                _write_json(
                    zf,
                    f"snapshots/v{version.version_no}-metadata_snapshot.json",
                    version.metadata_snapshot,
                    manifest_entries,
                )

        manifest = {
            "schema": "dms-revision-package-v1",
            "generated_at": generated_at.isoformat(),
            "document": {
                "id": document.id,
                "title": document.title,
                "asn": document.asn,
                "asn_label": format_asn(document.asn) if document.asn else None,
            },
            "archive_status": archive_report["status"],
            "entries": manifest_entries,
            "missing_files": missing_files,
        }
        _write_json(zf, "manifest.json", manifest, manifest_entries=None)

    filename = f"{slugify(document.title) or 'dokument'}-{document.id}-revisionspaket.zip"
    return RevisionPackage(
        filename=filename,
        content=buffer.getvalue(),
        manifest=manifest,
    )


def build_case_file_revision_package(case_file: CaseFile) -> RevisionPackage:
    """Baut ein ZIP-Revisionspaket für eine komplette Vorgangsakte."""
    generated_at = timezone.now()
    manifest_entries: list[dict[str, Any]] = []
    documents = list(
        case_file.documents.select_related(
            "correspondent",
            "document_type",
            "storage_path",
            "folder",
            "case_file",
            "owner",
            "current_version",
        )
        .prefetch_related("tags", "custom_field_values__field", "versions")
        .order_by("added_at", "id")
    )

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        _write_json(
            zf,
            "casefile-metadata.json",
            _case_file_metadata_payload(case_file, documents, generated_at=generated_at),
            manifest_entries,
        )
        _write_json(
            zf,
            "audit.json",
            _case_file_audit_payload(case_file),
            manifest_entries,
        )

        nested_packages = []
        for document in documents:
            package = build_document_revision_package(document)
            path = f"documents/{_document_package_name(document, package.filename)}"
            zf.writestr(path, package.content)
            manifest_entries.append(_entry(path, data=package.content, kind="zip"))
            nested_packages.append(
                {
                    "document_id": document.id,
                    "title": document.title,
                    "asn": document.asn,
                    "asn_label": format_asn(document.asn) if document.asn else None,
                    "path": path,
                    "archive_status": package.manifest.get("archive_status"),
                    "missing_files": package.manifest.get("missing_files", []),
                }
            )

        manifest = {
            "schema": "dms-casefile-revision-package-v1",
            "generated_at": generated_at.isoformat(),
            "case_file": {
                "id": case_file.id,
                "title": case_file.title,
                "status": case_file.status,
                "status_label": case_file.get_status_display(),
            },
            "document_count": len(documents),
            "documents": nested_packages,
            "entries": manifest_entries,
        }
        _write_json(zf, "manifest.json", manifest, manifest_entries=None)

    filename = f"{slugify(case_file.title) or 'akte'}-{case_file.id}-revisionspaket.zip"
    return RevisionPackage(filename=filename, content=buffer.getvalue(), manifest=manifest)


def _metadata_payload(document: Document, *, generated_at) -> dict[str, Any]:
    return {
        "generated_at": generated_at.isoformat(),
        "document": {
            "id": document.id,
            "title": document.title,
            "asn": document.asn,
            "asn_label": format_asn(document.asn) if document.asn else None,
            "created_at": _iso(document.created_at),
            "added_at": _iso(document.added_at),
            "correspondent": document.correspondent.name if document.correspondent else None,
            "document_type": document.document_type.name if document.document_type else None,
            "storage_path": document.storage_path.name if document.storage_path else None,
            "folder": document.folder.full_path if document.folder else None,
            "case_file": document.case_file.title if document.case_file else None,
            "owner": document.owner.username if document.owner else None,
            "status": document.status,
            "review_status": document.review_status,
            "retention_until": _iso(document.retention_until),
            "legal_hold": document.legal_hold,
            "legal_hold_reason": document.legal_hold_reason,
            "archive_status": document.archive_status,
            "archive_checked_at": _iso(document.archive_checked_at),
            "archive_error": document.archive_error,
        },
        "tags": [
            {"id": tag.id, "name": tag.name, "color": tag.color}
            for tag in document.tags.order_by("name", "id")
        ],
        "custom_fields": [
            {
                "field": item.field.name,
                "data_type": item.field.data_type,
                "value": item.value,
            }
            for item in document.custom_field_values.select_related("field").order_by(
                "field__name", "field_id"
            )
        ],
        "versions": [
            {
                "id": version.id,
                "version_no": version.version_no,
                "sha256": version.sha256,
                "prev_hash": version.prev_hash,
                "seal_hash": version.seal_hash,
                "snapshot_schema_version": version.snapshot_schema_version,
                "snapshot_taken_at": _iso(version.snapshot_taken_at),
                "is_immutable": version.is_immutable,
                "retention_until": _iso(version.retention_until),
                "processing_state": version.processing_state,
                "ocr_status": version.ocr_status,
                "mime_type": version.mime_type,
                "size": version.size,
                "page_count": version.page_count,
                "created_at": _iso(version.created_at),
            }
            for version in document.versions.order_by("version_no")
        ],
    }


def _retention_payload(document: Document, archive_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "retention": archive_report.get("retention"),
        "legal_hold": archive_report.get("legal_hold"),
        "document_retention_until": _iso(document.retention_until),
        "archive_status": archive_report.get("status"),
        "archive_warnings": archive_report.get("warnings", []),
        "archive_errors": archive_report.get("errors", []),
    }


def _audit_payload(document: Document) -> list[dict[str, Any]]:
    version_ids = [str(item.id) for item in document.versions.all()]
    entries = (
        AuditLogEntry.objects.select_related("actor")
        .filter(object_type="Document", object_id=str(document.id))
        | AuditLogEntry.objects.select_related("actor").filter(
            object_type="DocumentVersion",
            object_id__in=version_ids,
        )
    ).order_by("timestamp", "id")
    return [
        {
            "id": entry.id,
            "timestamp": _iso(entry.timestamp),
            "actor": entry.actor.username if entry.actor else None,
            "action": entry.action,
            "object_type": entry.object_type,
            "object_id": entry.object_id,
            "detail": entry.detail,
        }
        for entry in entries
    ]


def _case_file_metadata_payload(
    case_file: CaseFile,
    documents: list[Document],
    *,
    generated_at,
) -> dict[str, Any]:
    owner = case_file.owner
    return {
        "generated_at": generated_at.isoformat(),
        "case_file": {
            "id": case_file.id,
            "title": case_file.title,
            "description": case_file.description,
            "status": case_file.status,
            "status_label": case_file.get_status_display(),
            "owner": owner.username if owner else None,
            "ai_summary": case_file.ai_summary,
            "ai_summary_source": case_file.ai_summary_source,
            "ai_summary_generated_at": _iso(case_file.ai_summary_generated_at),
            "created_at": _iso(case_file.created_at),
            "updated_at": _iso(case_file.updated_at),
        },
        "documents": [
            {
                "id": document.id,
                "title": document.title,
                "asn": document.asn,
                "asn_label": format_asn(document.asn) if document.asn else None,
                "created_at": _iso(document.created_at),
                "added_at": _iso(document.added_at),
                "correspondent": document.correspondent.name
                if document.correspondent
                else None,
                "document_type": document.document_type.name
                if document.document_type
                else None,
                "folder": document.folder.full_path if document.folder else None,
                "archive_status": document.archive_status,
                "retention_until": _iso(document.retention_until),
                "legal_hold": document.legal_hold,
            }
            for document in documents
        ],
    }


def _case_file_audit_payload(case_file: CaseFile) -> list[dict[str, Any]]:
    entries = (
        AuditLogEntry.objects.select_related("actor")
        .filter(object_type="CaseFile", object_id=str(case_file.id))
        .order_by("timestamp", "id")
    )
    return [
        {
            "id": entry.id,
            "timestamp": _iso(entry.timestamp),
            "actor": entry.actor.username if entry.actor else None,
            "action": entry.action,
            "object_type": entry.object_type,
            "object_id": entry.object_id,
            "detail": entry.detail,
        }
        for entry in entries
    ]


def _write_json(
    zf: zipfile.ZipFile,
    name: str,
    payload,
    manifest_entries: list[dict[str, Any]] | None,
) -> None:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    zf.writestr(name, data)
    if manifest_entries is not None:
        manifest_entries.append(_entry(name, data=data))


def _write_text(
    zf: zipfile.ZipFile,
    name: str,
    text: str,
    manifest_entries: list[dict[str, Any]],
) -> None:
    data = text.encode("utf-8")
    zf.writestr(name, data)
    manifest_entries.append(_entry(name, data=data))


def _write_file(
    zf: zipfile.ZipFile,
    name: str,
    path: str,
    manifest_entries: list[dict[str, Any]],
    missing_files: list[dict[str, Any]],
) -> None:
    if not path or not os.path.exists(path):
        missing_files.append({"path": name, "source_path": path or "", "reason": "missing"})
        return

    hasher = hashlib.sha256()
    size = 0
    with open(path, "rb") as src, zf.open(name, "w") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
            hasher.update(chunk)
            size += len(chunk)
    manifest_entries.append(
        {
            "path": name,
            "kind": "file",
            "size": size,
            "sha256": hasher.hexdigest(),
            "source_basename": Path(path).name,
        }
    )


def _entry(name: str, *, data: bytes, kind: str | None = None) -> dict[str, Any]:
    return {
        "path": name,
        "kind": kind or ("json" if name.endswith(".json") else "text"),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _document_package_name(document: Document, fallback: str) -> str:
    asn = format_asn(document.asn) if document.asn else f"DOC{document.id}"
    title = slugify(document.title) or "dokument"
    suffix = Path(fallback).suffix or ".zip"
    return f"{asn}-{title}-{document.id}{suffix}"


def _suffix(path: str, *, fallback: str = "") -> str:
    suffix = Path(path or "").suffix
    return suffix or fallback


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
