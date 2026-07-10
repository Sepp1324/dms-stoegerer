"""Dokument-Qualitätscenter.

Der Service verdichtet vorhandene technische und fachliche Signale zu einem
handlungsorientierten Qualitätswert. Er ist bewusst deterministisch: keine KI,
keine externen Abhängigkeiten, keine neuen Tabellen. Die UI bekommt dadurch eine
verlässliche Liste der Dokumente, die zuerst menschliche Aufmerksamkeit brauchen.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from documents.models import Document, DocumentReviewTask, DocumentVersion, OCRStatus
from documents.services.asn import format_asn

ISSUE_LIMIT = 75

_FILENAMEISH_RE = re.compile(
    r"^(scan|img|image|document|dokument|rechnung)?[-_\s]?[0-9a-f]{8,}(\.[a-z0-9]+)?$",
    re.IGNORECASE,
)


def quality_status(
    documents: QuerySet[Document] | Iterable[Document],
    *,
    issue_limit: int = ISSUE_LIMIT,
) -> dict[str, Any]:
    """Liefert eine owner-gescopte Qualitätsübersicht für Dokumente.

    Die View-Schicht übergibt dasselbe Queryset wie die Dokumentliste. Dadurch
    gelten Owner-Isolation, Admin-Sicht und Filter identisch. Der Service selbst
    trifft nur Qualitätsaussagen über die sichtbaren Objekte.
    """
    qs = _prepare_queryset(documents)
    items = [document_quality(document) for document in qs]
    critical = [item for item in items if item["grade"] == "critical"]
    warnings = [item for item in items if item["grade"] == "warning"]
    average = round(sum(item["score"] for item in items) / len(items)) if items else 100
    status = "error" if critical else "warn" if warnings else "ok"

    issues = sorted(
        [item for item in items if item["issues"]],
        key=lambda item: (
            _severity_rank(item["status"]),
            item["score"],
            item["title"].lower(),
        ),
    )[:issue_limit]

    return {
        "status": status,
        "generated_at": timezone.now().isoformat(),
        "summary": {
            "documents": len(items),
            "average_score": average,
            "excellent": sum(1 for item in items if item["grade"] == "excellent"),
            "good": sum(1 for item in items if item["grade"] == "good"),
            "warning": len(warnings),
            "critical": len(critical),
            "ocr_issues": _count_category(items, "ocr"),
            "metadata_issues": _count_category(items, "metadata"),
            "archive_issues": _count_category(items, "archive"),
            "review_issues": _count_category(items, "review"),
        },
        "issues": issues,
    }


def document_quality(document: Document) -> dict[str, Any]:
    """Bewertet ein einzelnes Dokument anhand bestehender DMS-Signale."""
    current = document.current_version
    issues: list[dict[str, str]] = []
    score = 100

    if current is None:
        score -= _issue(
            issues,
            code="current_version_missing",
            category="archive",
            severity="error",
            message="Keine aktuelle Version vorhanden.",
            action="Dokument erneut importieren oder Version prüfen.",
            penalty=45,
        )
    else:
        score -= _processing_issues(issues, current)
        score -= _ocr_issues(issues, current)
        score -= _archive_artifact_issues(issues, current)

    metadata = _metadata_score(document)
    score -= _metadata_issues(issues, document, metadata)
    score -= _document_archive_issues(issues, document)
    score -= _review_issues(issues, document)

    score = max(0, min(100, score))
    status = _status(issues)
    grade = _grade(score, status)

    return {
        "document_id": document.id,
        "title": document.title,
        "asn": document.asn,
        "asn_label": format_asn(document.asn) if document.asn else None,
        "score": score,
        "grade": grade,
        "status": status,
        "summary": {
            "ocr": _ocr_summary(current),
            "metadata": metadata,
            "archive": _archive_summary(document, current),
            "review": _review_summary(document),
        },
        "issues": issues,
        "metrics": {
            "ocr_text_length": len((current.ocr_text if current else "") or ""),
            "page_count": current.page_count if current else None,
            "open_review_tasks": _open_review_task_count(document),
            "metadata_filled": metadata["completed"],
            "metadata_total": metadata["total"],
        },
        "archive_status": document.archive_status,
        "archive_status_label": document.get_archive_status_display(),
        "processing_state": current.processing_state if current else None,
        "ocr_status": current.ocr_status if current else None,
        "added_at": document.added_at.isoformat() if document.added_at else None,
        "created_at": document.created_at.isoformat() if document.created_at else None,
    }


def _prepare_queryset(documents: QuerySet[Document] | Iterable[Document]):
    if isinstance(documents, QuerySet):
        return documents.select_related(
            "current_version",
            "correspondent",
            "document_type",
            "storage_path",
            "folder",
            "case_file",
        ).prefetch_related("tags", "review_tasks")
    return documents


def _processing_issues(
    issues: list[dict[str, str]],
    current: DocumentVersion,
) -> int:
    PS = DocumentVersion.ProcessingState
    if current.processing_state == PS.FAILED:
        return _issue(
            issues,
            code="processing_failed",
            category="processing",
            severity="error",
            message="Dokumentverarbeitung ist fehlgeschlagen.",
            action="Verarbeitung neu starten und Fehlerdetails prüfen.",
            penalty=25,
        )
    if current.processing_state == PS.RETRY_PENDING:
        return _issue(
            issues,
            code="retry_pending",
            category="processing",
            severity="warn",
            message="Verarbeitungs-Retry wartet noch.",
            action="Worker-Status prüfen, falls der Zustand bestehen bleibt.",
            penalty=8,
        )
    if current.processing_state != PS.READY:
        return _issue(
            issues,
            code="processing_not_ready",
            category="processing",
            severity="warn",
            message="Dokument ist technisch noch nicht vollständig bereit.",
            action="Pipeline-Fortschritt beobachten.",
            penalty=10,
        )
    return 0


def _ocr_issues(issues: list[dict[str, str]], current: DocumentVersion) -> int:
    text = (current.ocr_text or "").strip()
    penalty = 0
    if current.ocr_status == OCRStatus.FAILED:
        penalty += _issue(
            issues,
            code="ocr_failed",
            category="ocr",
            severity="error",
            message=current.ocr_error or "OCR ist fehlgeschlagen.",
            action="OCR-Retry starten oder Quelldatei prüfen.",
            penalty=22,
        )
    elif current.ocr_status in {OCRStatus.PENDING, OCRStatus.RUNNING}:
        penalty += _issue(
            issues,
            code="ocr_unfinished",
            category="ocr",
            severity="warn",
            message="OCR ist noch nicht abgeschlossen.",
            action="Worker-Queue und OCR-Status prüfen.",
            penalty=10,
        )
    elif current.ocr_status == OCRStatus.SKIPPED:
        penalty += _issue(
            issues,
            code="ocr_skipped",
            category="ocr",
            severity="warn",
            message="OCR wurde übersprungen.",
            action="Prüfen, ob nativer Text vorhanden ist.",
            penalty=6,
        )

    if len(text) < 10:
        penalty += _issue(
            issues,
            code="ocr_text_empty",
            category="ocr",
            severity="warn",
            message="OCR-/Dokumenttext ist leer oder praktisch leer.",
            action="OCR erneut ausführen und Scanqualität prüfen.",
            penalty=15,
        )
    elif current.page_count and len(text) < current.page_count * 40:
        penalty += _issue(
            issues,
            code="ocr_text_short",
            category="ocr",
            severity="warn",
            message="OCR-Text wirkt für die Seitenanzahl ungewöhnlich kurz.",
            action="Stichprobe im Dokument prüfen.",
            penalty=8,
        )
    return penalty


def _archive_artifact_issues(
    issues: list[dict[str, str]],
    current: DocumentVersion,
) -> int:
    penalty = 0
    if not current.archive_path:
        penalty += _issue(
            issues,
            code="archive_pdf_missing",
            category="archive",
            severity="warn",
            message="Archiv-PDF/PDF-A fehlt.",
            action="Dokumentverarbeitung oder Archivprüfung erneut ausführen.",
            penalty=8,
        )
    elif not os.path.exists(current.archive_path):
        penalty += _issue(
            issues,
            code="archive_file_missing",
            category="archive",
            severity="error",
            message="Archivdatei ist in der Datenbank referenziert, aber nicht auf der Platte vorhanden.",
            action="Backup/Storage prüfen und Archivprüfung ausführen.",
            penalty=20,
        )

    if not current.thumbnail_path:
        penalty += _issue(
            issues,
            code="thumbnail_missing",
            category="archive",
            severity="warn",
            message="Vorschaubild fehlt.",
            action="Thumbnail neu erzeugen.",
            penalty=4,
        )
    elif not os.path.exists(current.thumbnail_path):
        penalty += _issue(
            issues,
            code="thumbnail_file_missing",
            category="archive",
            severity="warn",
            message="Vorschaubild ist referenziert, aber nicht auf der Platte vorhanden.",
            action="Thumbnail neu erzeugen.",
            penalty=5,
        )

    if not current.is_immutable:
        penalty += _issue(
            issues,
            code="worm_missing",
            category="archive",
            severity="warn",
            message="Aktuelle Version ist noch nicht unveränderlich versiegelt.",
            action="Sealing/Archivpipeline prüfen.",
            penalty=12,
        )
    if not current.seal_hash:
        penalty += _issue(
            issues,
            code="seal_missing",
            category="archive",
            severity="warn",
            message="Metadaten-/Versionssiegel fehlt.",
            action="Version versiegeln oder Snapshot backfillen.",
            penalty=12,
        )
    if not current.metadata_snapshot or current.snapshot_schema_version <= 0:
        penalty += _issue(
            issues,
            code="metadata_snapshot_missing",
            category="archive",
            severity="warn",
            message="Eingefrorener Metadaten-Snapshot fehlt.",
            action="Snapshot backfillen, damit spätere Änderungen vergleichbar sind.",
            penalty=7,
        )
    return penalty


def _metadata_score(document: Document) -> dict[str, Any]:
    checks = [
        ("title", bool(document.title.strip())),
        ("created_at", document.created_at is not None),
        ("correspondent", document.correspondent_id is not None),
        ("document_type", document.document_type_id is not None),
        ("folder", document.folder_id is not None),
        ("storage_path", document.storage_path_id is not None),
        ("tags", _tag_count(document) > 0),
    ]
    missing = [name for name, present in checks if not present]
    completed = len(checks) - len(missing)
    return {
        "completed": completed,
        "total": len(checks),
        "percent": round(completed / len(checks) * 100),
        "missing": missing,
    }


def _metadata_issues(
    issues: list[dict[str, str]],
    document: Document,
    metadata: dict[str, Any],
) -> int:
    penalty = 0
    missing_penalties = {
        "created_at": ("Belegdatum fehlt.", 5),
        "correspondent": ("Korrespondent fehlt.", 8),
        "document_type": ("Dokumenttyp fehlt.", 8),
        "folder": ("Ordner fehlt.", 4),
        "storage_path": ("Ablagepfad fehlt.", 4),
        "tags": ("Keine Tags vergeben.", 4),
    }
    for field in metadata["missing"]:
        if field == "title":
            continue
        message, points = missing_penalties.get(field, (f"{field} fehlt.", 4))
        penalty += _issue(
            issues,
            code=f"metadata_{field}_missing",
            category="metadata",
            severity="warn",
            message=message,
            action="Metadaten in der Dokumentdetailansicht ergänzen.",
            penalty=points,
        )

    if _looks_like_generated_title(document.title):
        penalty += _issue(
            issues,
            code="metadata_title_weak",
            category="metadata",
            severity="warn",
            message="Titel wirkt wie ein Dateiname oder Scan-Platzhalter.",
            action="Sprechenden Dokumenttitel vergeben.",
            penalty=5,
        )
    return penalty


def _document_archive_issues(
    issues: list[dict[str, str]],
    document: Document,
) -> int:
    if document.archive_status == Document.ArchiveStatus.OK:
        return 0
    if document.archive_status == Document.ArchiveStatus.ERROR:
        return _issue(
            issues,
            code="archive_check_error",
            category="archive",
            severity="error",
            message=document.archive_error or "Archivprüfung meldet Fehler.",
            action="Archivprüfung öffnen und Storage/Hash-Kette prüfen.",
            penalty=25,
        )
    if document.archive_status == Document.ArchiveStatus.WARNING:
        return _issue(
            issues,
            code="archive_check_warning",
            category="archive",
            severity="warn",
            message=document.archive_error or "Archivprüfung meldet Warnungen.",
            action="Archivbericht prüfen.",
            penalty=12,
        )
    return _issue(
        issues,
        code="archive_unchecked",
        category="archive",
        severity="warn",
        message="Archivprüfung wurde noch nicht ausgeführt.",
        action="Archivprüfung für das Dokument ausführen.",
        penalty=8,
    )


def _review_issues(issues: list[dict[str, str]], document: Document) -> int:
    open_tasks = _open_review_tasks(document)
    if not open_tasks and document.review_status == Document.ReviewStatus.REVIEWED:
        return 0
    penalty = 0
    if open_tasks:
        penalty += _issue(
            issues,
            code="review_tasks_open",
            category="review",
            severity="warn",
            message=f"{len(open_tasks)} offene Klärungsaufgabe(n).",
            action="Inbox-Aufgaben prüfen und erledigen.",
            penalty=min(15, len(open_tasks) * 5),
        )
    elif document.review_status == Document.ReviewStatus.NEEDS_REVIEW:
        penalty += _issue(
            issues,
            code="review_pending",
            category="review",
            severity="warn",
            message="Dokument ist fachlich noch nicht geprüft.",
            action="Metadaten prüfen und Dokument als geprüft markieren.",
            penalty=5,
        )
    return penalty


def _ocr_summary(current: DocumentVersion | None) -> dict[str, Any]:
    if current is None:
        return {
            "status": None,
            "status_label": "Keine Version",
            "text_length": 0,
            "page_count": None,
        }
    return {
        "status": current.ocr_status,
        "status_label": current.get_ocr_status_display(),
        "text_length": len(current.ocr_text or ""),
        "page_count": current.page_count,
    }


def _archive_summary(
    document: Document,
    current: DocumentVersion | None,
) -> dict[str, Any]:
    return {
        "status": document.archive_status,
        "status_label": document.get_archive_status_display(),
        "checked_at": document.archive_checked_at.isoformat()
        if document.archive_checked_at
        else None,
        "error": document.archive_error,
        "archive_file": bool(current and current.archive_path and os.path.exists(current.archive_path)),
        "thumbnail": bool(current and current.thumbnail_path and os.path.exists(current.thumbnail_path)),
        "immutable": bool(current and current.is_immutable),
        "sealed": bool(current and current.seal_hash),
        "metadata_snapshot": bool(
            current and current.metadata_snapshot and current.snapshot_schema_version > 0
        ),
    }


def _review_summary(document: Document) -> dict[str, Any]:
    open_tasks = _open_review_tasks(document)
    return {
        "status": document.review_status,
        "status_label": document.get_review_status_display(),
        "open_tasks": len(open_tasks),
        "top_tasks": [
            {
                "kind": task.kind,
                "kind_label": task.get_kind_display(),
                "message": task.message,
                "priority": task.priority,
            }
            for task in sorted(open_tasks, key=lambda item: (item.priority, item.created_at, item.id))[:3]
        ],
    }


def _open_review_tasks(document: Document) -> list[DocumentReviewTask]:
    tasks = list(document.review_tasks.all())
    return [task for task in tasks if task.status == DocumentReviewTask.Status.OPEN]


def _open_review_task_count(document: Document) -> int:
    return len(_open_review_tasks(document))


def _tag_count(document: Document) -> int:
    if hasattr(document, "_prefetched_objects_cache") and "tags" in document._prefetched_objects_cache:
        return len(document._prefetched_objects_cache["tags"])
    return document.tags.count()


def _looks_like_generated_title(title: str) -> bool:
    cleaned = title.strip()
    return not cleaned or bool(_FILENAMEISH_RE.match(cleaned))


def _issue(
    issues: list[dict[str, str]],
    *,
    code: str,
    category: str,
    severity: str,
    message: str,
    action: str,
    penalty: int,
) -> int:
    issues.append(
        {
            "code": code,
            "category": category,
            "severity": severity,
            "message": message[:500],
            "action": action,
        }
    )
    return penalty


def _status(issues: list[dict[str, str]]) -> str:
    if any(issue["severity"] == "error" for issue in issues):
        return "error"
    if issues:
        return "warn"
    return "ok"


def _grade(score: int, status: str) -> str:
    if status == "error" or score < 60:
        return "critical"
    if score < 80:
        return "warning"
    if score < 95:
        return "good"
    return "excellent"


def _severity_rank(status: str) -> int:
    return {"error": 0, "warn": 1, "ok": 2}.get(status, 3)


def _count_category(items: list[dict[str, Any]], category: str) -> int:
    return sum(
        1
        for item in items
        if any(issue["category"] == category for issue in item["issues"])
    )
