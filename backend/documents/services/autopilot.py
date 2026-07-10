"""Ablage-Autopilot fuer die Review-Inbox.

Der Autopilot ist keine zweite Klassifizierungs-Engine. Er verdichtet die
bereits vorhandenen Signale (Review-Tasks, KI-Vorschlaege, Extraktions- und
Aktenkandidaten) zu einer operativen Entscheidungsschicht: Was kann sofort
abgelegt werden, was braucht menschliche Pruefung, und warum?
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from documents.models import (
    CaseFileCandidate,
    Document,
    DocumentReviewTask,
    DocumentVersion,
    ExtractionCandidate,
    OCRStatus,
)
from documents.services.asn import format_asn


LANE_LABELS = {
    "ready": "Bereit zum Ablegen",
    "suggestions": "Vorschlaege pruefen",
    "metadata": "Metadaten fehlen",
    "processing": "In Verarbeitung",
    "error": "Fehlerhaft",
}

AI_FIELD_LABELS = {
    "title": "Titel",
    "date": "Belegdatum",
    "correspondent": "Korrespondent",
    "document_type": "Dokumenttyp",
    "tags": "Tags",
}


def _as_list(manager_or_iterable):
    """Nutzt Prefetch-Caches, faellt aber sauber auf QuerySets zurueck."""
    if hasattr(manager_or_iterable, "all"):
        return list(manager_or_iterable.all())
    return list(manager_or_iterable)


def _metadata_missing(document: Document) -> list[str]:
    missing = []
    if document.correspondent_id is None:
        missing.append("Korrespondent")
    if document.document_type_id is None:
        missing.append("Dokumenttyp")
    if document.storage_path_id is None:
        missing.append("Ablagepfad")
    if document.folder_id is None:
        missing.append("Ordner")
    return missing


def _open_review_tasks(document: Document) -> list[DocumentReviewTask]:
    tasks = [
        task
        for task in _as_list(document.review_tasks)
        if task.status == DocumentReviewTask.Status.OPEN
    ]
    return sorted(tasks, key=lambda task: (task.priority, task.created_at, task.id))


def _pending_extraction_candidates(document: Document) -> list[ExtractionCandidate]:
    candidates = [
        candidate
        for candidate in _as_list(document.extraction_candidates)
        if candidate.status == ExtractionCandidate.Status.PENDING
    ]
    return sorted(candidates, key=lambda item: (item.field, -item.confidence, item.id))


def _pending_case_candidates(document: Document) -> list[CaseFileCandidate]:
    candidates = [
        candidate
        for candidate in _as_list(document.case_file_candidates)
        if candidate.status == CaseFileCandidate.Status.PENDING
    ]
    return sorted(candidates, key=lambda item: (-item.score, item.id))


def _ai_suggestions(document: Document) -> list[dict]:
    suggestions = document.ai_suggestions or {}
    if not isinstance(suggestions, dict):
        return []

    items = []
    for key in ("title", "date", "correspondent", "document_type", "tags"):
        value = suggestions.get(key)
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            display = ", ".join(str(item) for item in value if str(item).strip())
        else:
            display = str(value).strip()
        if not display:
            continue
        items.append(
            {
                "kind": "ai",
                "field": key,
                "label": AI_FIELD_LABELS.get(key, key),
                "value": display[:240],
                "confidence": 70,
                "source": "KI",
                "action": {"type": "apply_ai_suggestion", "fields": [key]},
            }
        )
    return items


def _extraction_suggestions(candidates: list[ExtractionCandidate]) -> list[dict]:
    return [
        {
            "kind": "extraction",
            "field": candidate.field,
            "label": candidate.get_field_display(),
            "value": (candidate.normalized_value or candidate.value)[:240],
            "confidence": candidate.confidence,
            "source": candidate.source,
            "reason": candidate.reason,
            "action": {
                "type": "apply_extraction_candidate",
                "candidate_id": candidate.id,
            },
        }
        for candidate in candidates[:6]
    ]


def _case_suggestions(candidates: list[CaseFileCandidate]) -> list[dict]:
    items = []
    for candidate in candidates[:4]:
        target = (
            candidate.case_file.title
            if candidate.case_file_id
            else candidate.suggested_title
        )
        items.append(
            {
                "kind": "case_file",
                "field": "case_file",
                "label": candidate.get_kind_display(),
                "value": (target or "Neue Akte")[:240],
                "confidence": candidate.score,
                "source": candidate.source,
                "reason": candidate.reason,
                "action": {
                    "type": "apply_case_file_candidate",
                    "candidate_id": candidate.id,
                },
            }
        )
    return items


def _processing_reason(version: DocumentVersion | None) -> str | None:
    if version is None:
        return "Keine aktuelle Version vorhanden."
    if version.processing_state == DocumentVersion.ProcessingState.FAILED:
        return version.processing_error or "Dokumentverarbeitung ist fehlgeschlagen."
    if version.ocr_status == OCRStatus.FAILED:
        return version.ocr_error or "OCR ist fehlgeschlagen."
    if version.processing_state != DocumentVersion.ProcessingState.READY:
        return f"Verarbeitung laeuft noch ({version.get_processing_state_display()})."
    return None


def _score(
    *,
    version: DocumentVersion | None,
    missing: list[str],
    tasks: list[DocumentReviewTask],
    suggestions: list[dict],
    classification_rules: list[str],
) -> int:
    score = 100
    if version is None:
        score -= 40
    elif version.processing_state == DocumentVersion.ProcessingState.FAILED:
        score -= 45
    elif version.ocr_status == OCRStatus.FAILED:
        score -= 35
    elif version.processing_state != DocumentVersion.ProcessingState.READY:
        score -= 25

    score -= min(36, len(missing) * 9)
    if missing and not classification_rules:
        score -= 10
    score -= min(24, len(tasks) * 5)
    score -= min(18, len(suggestions) * 3)
    return max(0, min(100, score))


def _lane(
    *,
    version: DocumentVersion | None,
    missing: list[str],
    suggestions: list[dict],
    tasks: list[DocumentReviewTask],
) -> str:
    if version is None:
        return "error"
    if (
        version.processing_state == DocumentVersion.ProcessingState.FAILED
        or version.ocr_status == OCRStatus.FAILED
    ):
        return "error"
    if version.processing_state != DocumentVersion.ProcessingState.READY:
        return "processing"
    if missing:
        return "metadata"
    if suggestions or tasks:
        return "suggestions"
    return "ready"


def _next_actions(
    lane: str, *, suggestions: list[dict], missing: list[str]
) -> list[dict]:
    if lane == "error":
        return [{"kind": "retry", "label": "Verarbeitung pruefen", "tone": "danger"}]
    if lane == "processing":
        return [{"kind": "wait", "label": "Pipeline abwarten", "tone": "neutral"}]
    actions = []
    if suggestions:
        actions.append(
            {
                "kind": "apply_suggestions",
                "label": f"{len(suggestions)} Vorschlag/Vorschlaege pruefen",
                "tone": "primary",
            }
        )
    if missing:
        actions.append(
            {
                "kind": "complete_metadata",
                "label": f"{len(missing)} Metadatum/Metadaten ergaenzen",
                "tone": "warn",
            }
        )
    if not actions:
        actions.append(
            {
                "kind": "mark_reviewed",
                "label": "Als geprueft ablegen",
                "tone": "ok",
            }
        )
    return actions


def build_item(document: Document) -> dict:
    """Erstellt die Autopilot-Entscheidung fuer ein Dokument."""
    version = document.current_version
    missing = _metadata_missing(document)
    tasks = _open_review_tasks(document)
    extraction_candidates = _pending_extraction_candidates(document)
    case_candidates = _pending_case_candidates(document)
    suggestions = [
        *_ai_suggestions(document),
        *_extraction_suggestions(extraction_candidates),
        *_case_suggestions(case_candidates),
    ]
    classification_rules = (document.classification or {}).get("rules") or []
    processing_reason = _processing_reason(version)
    lane = _lane(
        version=version,
        missing=missing,
        suggestions=suggestions,
        tasks=tasks,
    )
    score = _score(
        version=version,
        missing=missing,
        tasks=tasks,
        suggestions=suggestions,
        classification_rules=classification_rules,
    )

    reasons = []
    if processing_reason:
        reasons.append(processing_reason)
    if missing:
        reasons.append(f"Fehlende Metadaten: {', '.join(missing)}.")
    if classification_rules:
        reasons.append(
            "Regelklassifizierung: "
            + ", ".join(str(rule) for rule in classification_rules[:3])
        )
    elif missing:
        reasons.append("Keine Klassifizierungsregel hat sicher gegriffen.")
    if document.ai_suggestions:
        reasons.append("KI-Vorschlaege warten auf Entscheidung.")
    if extraction_candidates:
        reasons.append(
            f"{len(extraction_candidates)} Strukturvorschlag/Vorschlaege offen."
        )
    if case_candidates:
        reasons.append(f"{len(case_candidates)} Aktenvorschlag/Vorschlaege offen.")
    if not reasons:
        reasons.append("Metadaten vollstaendig, keine offenen Klaerungspunkte.")

    can_autofile = lane == "ready" and score >= 90
    return {
        "document": document.id,
        "title": document.title,
        "asn_label": format_asn(document.asn) if document.asn else None,
        "lane": lane,
        "lane_label": LANE_LABELS[lane],
        "confidence": score,
        "can_autofile": can_autofile,
        "bulk_safe": can_autofile,
        "missing_metadata": missing,
        "suggestions": suggestions,
        "reasons": reasons[:5],
        "next_actions": _next_actions(lane, suggestions=suggestions, missing=missing),
        "signals": {
            "review_tasks": len(tasks),
            "ai_suggestions": len(_ai_suggestions(document)),
            "extraction_candidates": len(extraction_candidates),
            "case_candidates": len(case_candidates),
            "classification_rules": len(classification_rules),
        },
    }


def build_inbox(documents: Iterable[Document], *, total: int | None = None) -> dict:
    items = [build_item(document) for document in documents]
    lanes = Counter(item["lane"] for item in items)
    avg = round(sum(item["confidence"] for item in items) / len(items)) if items else 0
    return {
        "total": total if total is not None else len(items),
        "items": items,
        "summary": {
            "lanes": {key: lanes.get(key, 0) for key in LANE_LABELS},
            "average_confidence": avg,
            "auto_ready": sum(1 for item in items if item["can_autofile"]),
            "needs_human": sum(1 for item in items if not item["can_autofile"]),
            "pending_suggestions": sum(len(item["suggestions"]) for item in items),
        },
    }
