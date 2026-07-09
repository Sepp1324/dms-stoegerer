"""Dokument-Briefing: verdichtet vorhandene DMS-Signale zu nächsten Schritten.

Der Service ist bewusst deterministisch. Er ruft keinen externen KI-Provider
auf, sondern fasst vorhandene Signale aus OCR, Review-Inbox, Wiedervorlagen,
Verträgen, Akten, Entitäten und Audit zusammen. Damit ist das Briefing schnell,
testbar und auch dann wertvoll, wenn die KI-Anbindung gerade nicht verfügbar ist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from documents.models import (
    AuditLogEntry,
    CaseFileCandidate,
    ContractRecord,
    Document,
    DocumentReminder,
    DocumentReviewTask,
    DocumentVersion,
    ExtractionCandidate,
)
from documents.services import archive as archive_service
from documents.services.asn import format_asn


@dataclass(frozen=True)
class BriefingAction:
    kind: str
    priority: int
    title: str
    description: str
    action_label: str
    target: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "priority": self.priority,
            "title": self.title,
            "description": self.description,
            "action_label": self.action_label,
            "target": self.target,
        }


def build_document_briefing(
    document: Document,
    *,
    visible_documents: QuerySet[Document] | None = None,
) -> dict[str, Any]:
    """Erstellt ein handlungsorientiertes Briefing für ein sichtbares Dokument."""

    current_version = document.current_version
    text = (current_version.ocr_text if current_version else "") or ""
    open_tasks = list(
        document.review_tasks.filter(status=DocumentReviewTask.Status.OPEN).order_by(
            "priority", "created_at", "id"
        )
    )
    pending_reminders = list(
        document.reminders.filter(done=False).order_by("remind_on", "id")
    )
    extraction_candidates = list(
        document.extraction_candidates.filter(
            status=ExtractionCandidate.Status.PENDING
        ).order_by("-confidence", "field", "id")[:8]
    )
    case_candidates = list(
        document.case_file_candidates.filter(
            status=CaseFileCandidate.Status.PENDING
        ).order_by("-score", "id")[:5]
    )
    contract = _contract_payload(document)
    retention = archive_service.retention_state(document)
    metadata_score = _metadata_score(document)
    actions = _next_actions(
        document,
        current_version=current_version,
        open_tasks=open_tasks,
        pending_reminders=pending_reminders,
        extraction_candidates=extraction_candidates,
        case_candidates=case_candidates,
        contract=contract,
        retention=retention,
        metadata_score=metadata_score,
    )
    risks = _risks(
        document,
        current_version=current_version,
        open_tasks=open_tasks,
        pending_reminders=pending_reminders,
        contract=contract,
        retention=retention,
    )
    risk_level = _risk_level(risks, actions)

    return {
        "document": _document_payload(document, current_version),
        "summary": _summary(document, text),
        "risk_level": risk_level,
        "metadata_score": metadata_score,
        "health": _health_payload(document, current_version, retention),
        "next_actions": [action.as_dict() for action in actions[:8]],
        "risks": risks,
        "signals": _signals_payload(
            document,
            text=text,
            open_tasks=open_tasks,
            pending_reminders=pending_reminders,
            extraction_candidates=extraction_candidates,
            case_candidates=case_candidates,
            contract=contract,
        ),
        "timeline": _timeline(document, current_version, pending_reminders, contract),
        "relations": _relations(document, visible_documents=visible_documents),
        "audit": _audit(document),
        "generated_at": timezone.now().isoformat(),
    }


def _document_payload(
    document: Document,
    current_version: DocumentVersion | None,
) -> dict[str, Any]:
    return {
        "id": document.id,
        "title": document.title,
        "asn": document.asn,
        "asn_label": format_asn(document.asn) if document.asn else None,
        "status": document.status,
        "status_label": document.get_status_display(),
        "review_status": document.review_status,
        "review_status_label": document.get_review_status_display(),
        "correspondent": document.correspondent.name if document.correspondent else None,
        "document_type": document.document_type.name if document.document_type else None,
        "folder": document.folder.full_path if document.folder else None,
        "case_file": (
            {
                "id": document.case_file_id,
                "title": document.case_file.title,
                "status": document.case_file.status,
                "status_label": document.case_file.get_status_display(),
            }
            if document.case_file
            else None
        ),
        "page_count": current_version.page_count if current_version else None,
        "added_at": _iso(document.added_at),
        "created_at": _iso(document.created_at),
    }


def _summary(document: Document, text: str) -> dict[str, Any]:
    suggestion_summary = (document.ai_suggestions or {}).get("summary")
    if suggestion_summary:
        return {"source": "ai_suggestions", "text": str(suggestion_summary).strip()}

    fallback = _first_sentences(text)
    if fallback:
        return {"source": "ocr", "text": fallback}

    parts = [
        document.title,
        document.correspondent.name if document.correspondent else "",
        document.document_type.name if document.document_type else "",
    ]
    return {
        "source": "metadata",
        "text": " · ".join(part for part in parts if part) or "Noch keine Zusammenfassung verfügbar.",
    }


def _metadata_score(document: Document) -> dict[str, Any]:
    fields = [
        ("title", bool(document.title.strip())),
        ("created_at", document.created_at is not None),
        ("correspondent", document.correspondent_id is not None),
        ("document_type", document.document_type_id is not None),
        ("folder", document.folder_id is not None),
        ("case_file", document.case_file_id is not None),
        ("tags", document.tags.exists()),
    ]
    missing = [name for name, present in fields if not present]
    completed = len(fields) - len(missing)
    return {
        "completed": completed,
        "total": len(fields),
        "percent": round(completed / len(fields) * 100),
        "missing": missing,
    }


def _health_payload(
    document: Document,
    current_version: DocumentVersion | None,
    retention: dict[str, Any],
) -> dict[str, Any]:
    return {
        "processing_state": current_version.processing_state if current_version else None,
        "ocr_status": current_version.ocr_status if current_version else None,
        "ocr_error": current_version.ocr_error if current_version else "",
        "archive_status": document.archive_status,
        "archive_status_label": document.get_archive_status_display(),
        "archive_error": document.archive_error,
        "retention": retention,
        "legal_hold": document.legal_hold,
        "legal_hold_reason": document.legal_hold_reason,
        "sealed": bool(current_version and current_version.seal_hash),
        "immutable": bool(current_version and current_version.is_immutable),
    }


def _signals_payload(
    document: Document,
    *,
    text: str,
    open_tasks: list[DocumentReviewTask],
    pending_reminders: list[DocumentReminder],
    extraction_candidates: list[ExtractionCandidate],
    case_candidates: list[CaseFileCandidate],
    contract: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ocr": {
            "characters": len(text),
            "words": len(re.findall(r"\w+", text)),
            "has_text": bool(text.strip()),
        },
        "review_tasks": [
            {
                "id": task.id,
                "kind": task.kind,
                "kind_label": task.get_kind_display(),
                "priority": task.priority,
                "message": task.message,
                "suggested_action": task.suggested_action,
            }
            for task in open_tasks
        ],
        "reminders": [
            {
                "id": reminder.id,
                "remind_on": reminder.remind_on.isoformat(),
                "note": reminder.note,
                "due": reminder.remind_on <= timezone.localdate(),
            }
            for reminder in pending_reminders[:6]
        ],
        "extraction_candidates": [
            {
                "id": candidate.id,
                "field": candidate.field,
                "field_label": candidate.get_field_display(),
                "value": candidate.value,
                "confidence": candidate.confidence,
            }
            for candidate in extraction_candidates
        ],
        "case_candidates": [
            {
                "id": candidate.id,
                "kind": candidate.kind,
                "kind_label": candidate.get_kind_display(),
                "target": candidate.case_file.title
                if candidate.case_file
                else candidate.suggested_title,
                "score": candidate.score,
                "reason": candidate.reason,
            }
            for candidate in case_candidates
        ],
        "contract": contract,
        "ai_suggestions": {
            key: value
            for key, value in (document.ai_suggestions or {}).items()
            if value not in (None, "", [])
        },
    }


def _contract_payload(document: Document) -> dict[str, Any] | None:
    try:
        contract = document.contract_record
    except ContractRecord.DoesNotExist:
        return None
    return {
        "id": contract.id,
        "provider": contract.provider,
        "provider_display": contract.provider
        or (document.correspondent.name if document.correspondent else ""),
        "contract_type": contract.contract_type,
        "contract_type_label": contract.get_contract_type_display(),
        "contract_number": contract.contract_number,
        "amount": str(contract.amount) if contract.amount is not None else None,
        "currency": contract.currency,
        "status": contract.status,
        "status_label": contract.get_status_display(),
        "needs_review": contract.needs_review,
        "cancel_until": _iso(contract.cancel_until),
        "next_due_on": _iso(contract.next_due_on),
        "ends_on": _iso(contract.ends_on),
    }


def _next_actions(
    document: Document,
    *,
    current_version: DocumentVersion | None,
    open_tasks: list[DocumentReviewTask],
    pending_reminders: list[DocumentReminder],
    extraction_candidates: list[ExtractionCandidate],
    case_candidates: list[CaseFileCandidate],
    contract: dict[str, Any] | None,
    retention: dict[str, Any],
    metadata_score: dict[str, Any],
) -> list[BriefingAction]:
    actions: list[BriefingAction] = []
    for task in open_tasks[:5]:
        actions.append(
            BriefingAction(
                kind=f"review_task:{task.kind}",
                priority=task.priority,
                title=task.get_kind_display(),
                description=task.message,
                action_label=task.suggested_action or "In Inbox prüfen",
                target="overview",
            )
        )

    if current_version and current_version.processing_state == DocumentVersion.ProcessingState.FAILED:
        actions.append(
            BriefingAction(
                kind="processing_failed",
                priority=5,
                title="Verarbeitung fehlgeschlagen",
                description=current_version.processing_error or "Pipeline-Fehler prüfen.",
                action_label="Retry starten",
                target="overview",
            )
        )

    if extraction_candidates:
        actions.append(
            BriefingAction(
                kind="extraction_candidates",
                priority=25,
                title="Strukturdaten prüfen",
                description=f"{len(extraction_candidates)} offene Kandidaten vorhanden.",
                action_label="Inbox öffnen",
                target="overview",
            )
        )

    if case_candidates:
        actions.append(
            BriefingAction(
                kind="case_candidates",
                priority=28,
                title="Aktenvorschlag prüfen",
                description=f"{len(case_candidates)} mögliche Aktenzuordnung vorhanden.",
                action_label="Aktenzuordnung prüfen",
                target="overview",
            )
        )

    if contract and contract["needs_review"]:
        actions.append(
            BriefingAction(
                kind="contract_review",
                priority=20,
                title="Vertrag prüfen",
                description="Der Contract-Center-Datensatz ist noch nicht bestätigt.",
                action_label="Contract Center öffnen",
                target="overview",
            )
        )

    due = [item for item in pending_reminders if item.remind_on <= timezone.localdate()]
    if due:
        actions.append(
            BriefingAction(
                kind="reminder_due",
                priority=15,
                title="Wiedervorlage fällig",
                description=f"{len(due)} Erinnerung(en) sind fällig oder überfällig.",
                action_label="Wiedervorlage öffnen",
                target="reminder",
            )
        )

    if document.archive_status == Document.ArchiveStatus.ERROR:
        actions.append(
            BriefingAction(
                kind="archive_error",
                priority=8,
                title="Archivprüfung fehlgeschlagen",
                description=document.archive_error or "Archivintegrität prüfen.",
                action_label="Archiv prüfen",
                target="overview",
            )
        )
    elif document.archive_status == Document.ArchiveStatus.WARNING:
        actions.append(
            BriefingAction(
                kind="archive_warning",
                priority=35,
                title="Archivwarnung vorhanden",
                description=document.archive_error or "Archivprüfung hat Warnungen gemeldet.",
                action_label="Archiv prüfen",
                target="overview",
            )
        )

    if retention.get("state") == "expired" and not document.legal_hold:
        actions.append(
            BriefingAction(
                kind="retention_expired",
                priority=30,
                title="Aufbewahrung abgelaufen",
                description="Die Aufbewahrungsfrist ist abgelaufen; fachliche Entscheidung nötig.",
                action_label="Archivstatus prüfen",
                target="overview",
            )
        )

    if metadata_score["percent"] < 70:
        actions.append(
            BriefingAction(
                kind="metadata_incomplete",
                priority=45,
                title="Metadaten unvollständig",
                description="Fehlt: " + ", ".join(metadata_score["missing"]),
                action_label="Metadaten bearbeiten",
                target="overview",
            )
        )

    if (document.ai_suggestions or {}) and document.review_status == Document.ReviewStatus.NEEDS_REVIEW:
        actions.append(
            BriefingAction(
                kind="ai_suggestions_pending",
                priority=40,
                title="KI-Vorschläge offen",
                description="Es liegen übernehmbare Vorschläge für dieses Dokument vor.",
                action_label="KI-Vorschläge prüfen",
                target="ai",
            )
        )

    return sorted(actions, key=lambda action: (action.priority, action.kind))


def _risks(
    document: Document,
    *,
    current_version: DocumentVersion | None,
    open_tasks: list[DocumentReviewTask],
    pending_reminders: list[DocumentReminder],
    contract: dict[str, Any] | None,
    retention: dict[str, Any],
) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    if current_version and current_version.processing_state == DocumentVersion.ProcessingState.FAILED:
        risks.append({"level": "high", "label": "Pipeline-Fehler", "detail": current_version.processing_error})
    if document.archive_status == Document.ArchiveStatus.ERROR:
        risks.append({"level": "high", "label": "Archivfehler", "detail": document.archive_error})
    if any(task.priority <= 20 for task in open_tasks):
        risks.append({"level": "medium", "label": "Wichtige Review-Aufgabe", "detail": open_tasks[0].message})
    if any(reminder.remind_on <= timezone.localdate() for reminder in pending_reminders):
        risks.append({"level": "medium", "label": "Fällige Wiedervorlage", "detail": "Mindestens eine Erinnerung ist fällig."})
    if contract and contract["needs_review"]:
        risks.append({"level": "medium", "label": "Vertrag ungeprüft", "detail": "Vertragsdaten sind noch nicht bestätigt."})
    if retention.get("state") == "expired" and not document.legal_hold:
        risks.append({"level": "medium", "label": "Aufbewahrung abgelaufen", "detail": "Entscheidung über weitere Aufbewahrung nötig."})
    if document.legal_hold:
        risks.append({"level": "info", "label": "Legal Hold", "detail": document.legal_hold_reason or "Löschsperre aktiv."})
    return risks


def _risk_level(risks: list[dict[str, Any]], actions: list[BriefingAction]) -> str:
    if any(risk["level"] == "high" for risk in risks) or any(action.priority <= 10 for action in actions):
        return "high"
    if any(risk["level"] == "medium" for risk in risks) or any(action.priority <= 35 for action in actions):
        return "medium"
    if actions:
        return "low"
    return "clear"


def _timeline(
    document: Document,
    current_version: DocumentVersion | None,
    reminders: list[DocumentReminder],
    contract: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    items = [
        {"kind": "added", "label": "Im DMS erfasst", "date": _iso(document.added_at)},
    ]
    if document.created_at:
        items.append({"kind": "document_date", "label": "Dokumentdatum", "date": _iso(document.created_at)})
    if current_version:
        items.append({"kind": "version", "label": f"Version {current_version.version_no}", "date": _iso(current_version.created_at)})
    if contract:
        for key, label in (
            ("cancel_until", "Kündigungsfrist"),
            ("next_due_on", "Nächste Fälligkeit"),
            ("ends_on", "Vertragsende"),
        ):
            if contract.get(key):
                items.append({"kind": key, "label": label, "date": contract[key]})
    for reminder in reminders[:4]:
        items.append({"kind": "reminder", "label": reminder.note or "Wiedervorlage", "date": reminder.remind_on.isoformat()})
    return sorted(items, key=lambda item: item["date"] or "")


def _relations(
    document: Document,
    *,
    visible_documents: QuerySet[Document] | None,
) -> dict[str, Any]:
    entities = [
        {
            "id": link.entity_id,
            "name": link.entity.name,
            "kind": link.entity.kind,
            "kind_label": link.entity.get_kind_display(),
            "role": link.role,
            "role_label": link.get_role_display(),
            "confidence": link.confidence,
        }
        for link in document.entity_links.select_related("entity").order_by(
            "role", "-confidence", "entity__name"
        )[:8]
    ]
    related_documents: list[dict[str, Any]] = []
    if visible_documents is not None:
        qs = visible_documents.exclude(pk=document.pk)
        if document.case_file_id:
            qs = qs.filter(case_file_id=document.case_file_id)
        elif document.correspondent_id:
            qs = qs.filter(correspondent_id=document.correspondent_id)
        else:
            qs = qs.none()
        related_documents = [
            {
                "id": item.id,
                "title": item.title,
                "reason": "gleiche Akte" if document.case_file_id else "gleicher Korrespondent",
                "added_at": _iso(item.added_at),
            }
            for item in qs.order_by("-added_at", "-id")[:5]
        ]
    return {"entities": entities, "related_documents": related_documents}


def _audit(document: Document) -> list[dict[str, Any]]:
    version_ids = [str(item.id) for item in document.versions.all()]
    entries = (
        AuditLogEntry.objects.select_related("actor")
        .filter(object_type="Document", object_id=str(document.id))
        | AuditLogEntry.objects.select_related("actor").filter(
            object_type="DocumentVersion",
            object_id__in=version_ids,
        )
    ).order_by("-timestamp", "-id")[:6]
    return [
        {
            "id": entry.id,
            "timestamp": _iso(entry.timestamp),
            "actor": entry.actor.get_username() if entry.actor else None,
            "action": entry.action,
            "detail": entry.detail,
        }
        for entry in entries
    ]


def _first_sentences(text: str, *, limit: int = 420) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    summary = " ".join(parts[:2]).strip()
    if len(summary) > limit:
        return summary[: limit - 1].rstrip() + "…"
    return summary


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
