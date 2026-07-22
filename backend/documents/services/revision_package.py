"""Revisionspaket-Export für einzelne Dokumente.

Das Paket ist bewusst selbsterklärend aufgebaut: Dateien liegen unter
``files/``, OCR-Text unter ``text/`` und alle prüfbaren Begleitdaten als JSON im
Root. ``manifest.json`` enthält SHA-256 und Größe jedes Paketbestandteils.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.utils import timezone
from django.utils.text import slugify

from documents.models import AuditLogEntry, Document
from documents.services import archive as archive_service
from documents.services.asn import format_asn


@dataclass(frozen=True)
class RevisionPackage:
    filename: str
    path: str  # Pfad zur fertigen ZIP-Datei auf der Platte (per FileResponse streamen)
    manifest: dict[str, Any]


def build_document_revision_package(document: Document) -> RevisionPackage:
    """Baut ein ZIP-Revisionspaket für ein sichtbares Dokument.

    Das ZIP wird direkt in eine temporäre Datei auf der Platte geschrieben (nicht
    in den RAM) und per :class:`~django.http.FileResponse` gestreamt. Große oder
    stark versionierte Dokumente blockieren so nicht den Web-Prozess durch
    mehrfaches Vollkopieren im Speicher. Der Aufrufer ist für das Aufräumen der
    Datei zuständig (``path``); die View entfernt sie nach dem Öffnen (unlink).
    """
    generated_at = timezone.now()
    archive_report = archive_service.verify_document_archive(document, persist=False)
    manifest_entries: list[dict[str, Any]] = []
    missing_files: list[dict[str, Any]] = []

    fd, tmp_path = tempfile.mkstemp(prefix="dms-revpkg-", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
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
            path=tmp_path,
            manifest=manifest,
        )
    except BaseException:
        # Bei jedem Fehler die halbfertige Temp-ZIP entfernen (kein /tmp-Leak) und
        # weiterwerfen – so wird auch KEIN Export-Audit geschrieben (View-Reihenfolge).
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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


def _entry(name: str, *, data: bytes) -> dict[str, Any]:
    return {
        "path": name,
        "kind": "json" if name.endswith(".json") else "text",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _suffix(path: str, *, fallback: str = "") -> str:
    suffix = Path(path or "").suffix
    return suffix or fallback


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
