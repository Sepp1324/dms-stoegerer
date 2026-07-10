"""Semantische Dokument-Timeline aus dem append-only Audit-Log.

Das rohe ``AuditLogEntry`` bleibt die Beweisquelle. Dieser Service formt daraus
eine stabile, UI-taugliche Timeline mit Kategorie, Titel, Zusammenfassung und
Severity, ohne Daten zu verlieren.
"""
from __future__ import annotations

from typing import Any

from django.db.models import Q

from documents.models import AuditLogEntry, Document

TimelineItem = dict[str, Any]

CATEGORY_LABELS = {
    "processing": "Verarbeitung",
    "metadata": "Metadaten",
    "workflow": "Workflow",
    "security": "Sicherheit",
    "archive": "Archiv",
    "export": "Export",
    "system": "System",
}

FIELD_LABELS = {
    "title": "Titel",
    "correspondent": "Korrespondent",
    "document_type": "Typ",
    "storage_path": "Ablagepfad",
    "folder": "Ordner",
    "case_file": "Akte",
    "tags": "Schlagworte",
    "created_at": "Belegdatum",
    "review_status": "Review-Status",
    "status": "Freigabestatus",
}

ACTION_TITLES = {
    "upload": "Dokument hochgeladen",
    "add_version": "Neue Version angelegt",
    "ocr": "OCR abgeschlossen",
    "processing_state": "Verarbeitungsstatus geändert",
    "processing_failed": "Verarbeitung fehlgeschlagen",
    "processing_retry": "Verarbeitung erneut geplant",
    "processing_resume": "Verarbeitung fortgesetzt",
    "immutable_set": "Version WORM-versiegelt",
    "metadata_snapshot": "Metadaten-Snapshot geschrieben",
    "classify": "Regelklassifizierung angewendet",
    "bulk_classify": "Sammelklassifizierung ausgeführt",
    "update": "Metadaten geändert",
    "bulk_update": "Sammeländerung ausgeführt",
    "apply_suggestions": "KI-Vorschläge übernommen",
    "suggest": "KI-Vorschläge erzeugt",
    "dismiss_suggestions": "KI-Vorschläge verworfen",
    "generate_extraction_candidates": "Strukturdaten erkannt",
    "apply_extraction_candidate": "Strukturwert übernommen",
    "dismiss_extraction_candidate": "Strukturwert verworfen",
    "generate_case_file_candidates": "Aktenvorschlag erzeugt",
    "apply_case_file_candidate": "Aktenvorschlag übernommen",
    "dismiss_case_file_candidate": "Aktenvorschlag verworfen",
    "mark_reviewed": "Review abgeschlossen",
    "mark_reviewed_bulk": "Review gesammelt abgeschlossen",
    "review_task_resolve": "Prüfaufgabe erledigt",
    "create_classification_rule_from_review": "Regel aus Review gelernt",
    "submit": "Zur Freigabe eingereicht",
    "approve": "Freigegeben",
    "reject": "Abgelehnt",
    "workflow": "Workflow ausgeführt",
    "reminder_created": "Wiedervorlage erstellt",
    "reminder_done": "Wiedervorlage erledigt",
    "archive_check": "Archivprüfung ausgeführt",
    "archive_bulk_check": "Archivprüfung gesammelt ausgeführt",
    "legal_hold": "Legal Hold geändert",
    "legal_hold_block": "Löschung durch Legal Hold blockiert",
    "immutable_block": "Löschung durch WORM blockiert",
    "retention_block": "Löschung durch Retention blockiert",
    "share_link_create": "Freigabelink erstellt",
    "share_link_revoke": "Freigabelink widerrufen",
    "share_download": "Freigabe heruntergeladen",
    "revision_package_export": "Revisionspaket exportiert",
    "ask": "Copilot-Frage gestellt",
    "semantic_reindex": "Semantischer Index aktualisiert",
    "asn_claim": "ASN zugeordnet",
    "asn_match": "ASN aus Dokument erkannt",
}

CATEGORY_BY_ACTION = {
    "upload": "processing",
    "add_version": "processing",
    "ocr": "processing",
    "processing_state": "processing",
    "processing_failed": "processing",
    "processing_retry": "processing",
    "processing_resume": "processing",
    "immutable_set": "archive",
    "metadata_snapshot": "archive",
    "classify": "metadata",
    "bulk_classify": "metadata",
    "update": "metadata",
    "bulk_update": "metadata",
    "apply_suggestions": "metadata",
    "suggest": "metadata",
    "dismiss_suggestions": "metadata",
    "generate_extraction_candidates": "metadata",
    "apply_extraction_candidate": "metadata",
    "dismiss_extraction_candidate": "metadata",
    "generate_case_file_candidates": "metadata",
    "apply_case_file_candidate": "metadata",
    "dismiss_case_file_candidate": "metadata",
    "mark_reviewed": "workflow",
    "mark_reviewed_bulk": "workflow",
    "review_task_resolve": "workflow",
    "create_classification_rule_from_review": "metadata",
    "submit": "workflow",
    "approve": "workflow",
    "reject": "workflow",
    "workflow": "workflow",
    "reminder_created": "workflow",
    "reminder_done": "workflow",
    "archive_check": "archive",
    "archive_bulk_check": "archive",
    "legal_hold": "security",
    "legal_hold_block": "security",
    "immutable_block": "security",
    "retention_block": "security",
    "share_link_create": "security",
    "share_link_revoke": "security",
    "share_download": "security",
    "revision_package_export": "export",
    "ask": "system",
    "semantic_reindex": "system",
    "asn_claim": "archive",
    "asn_match": "archive",
}


def build_document_timeline(document: Document, *, limit: int = 150) -> dict[str, Any]:
    """Aggregiert Dokument- und Versions-Audit zu einer verständlichen Timeline."""
    version_ids = [str(version.id) for version in document.versions.all()]
    entries = (
        AuditLogEntry.objects.filter(
            Q(object_type="Document", object_id=str(document.id))
            | Q(object_type="DocumentVersion", object_id__in=version_ids)
        )
        .select_related("actor")
        .order_by("-timestamp", "-id")
    )
    total = entries.count()
    items = [_normalize_entry(entry) for entry in entries[:limit]]
    return {
        "count": total,
        "limit": limit,
        "truncated": total > limit,
        "categories": [
            {"id": key, "label": label}
            for key, label in CATEGORY_LABELS.items()
            if any(item["category"] == key for item in items)
        ],
        "results": items,
    }


def _normalize_entry(entry: AuditLogEntry) -> TimelineItem:
    detail = entry.detail or {}
    category = CATEGORY_BY_ACTION.get(entry.action, _category_from_action(entry.action))
    severity = _severity(entry.action, detail)
    return {
        "id": entry.id,
        "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
        "actor": entry.actor_id,
        "actor_name": _actor_name(entry),
        "category": category,
        "category_label": CATEGORY_LABELS.get(category, "System"),
        "action": entry.action,
        "object_type": entry.object_type,
        "object_id": entry.object_id,
        "title": ACTION_TITLES.get(entry.action, _humanize(entry.action)),
        "summary": _summary(entry.action, detail, entry.object_type),
        "detail": detail,
        "severity": severity,
    }


def _actor_name(entry: AuditLogEntry) -> str:
    if entry.actor is None:
        return "System"
    return entry.actor.get_full_name() or entry.actor.get_username()


def _category_from_action(action: str) -> str:
    if "workflow" in action or "review" in action or "reminder" in action:
        return "workflow"
    if "archive" in action or "retention" in action or "immutable" in action:
        return "archive"
    if "share" in action or "hold" in action or "owner" in action:
        return "security"
    if "export" in action or "download" in action:
        return "export"
    if "ocr" in action or "processing" in action or "version" in action:
        return "processing"
    if "classify" in action or "suggest" in action or "update" in action:
        return "metadata"
    return "system"


def _severity(action: str, detail: dict[str, Any]) -> str:
    if action in {"processing_failed"}:
        return "error"
    if action in {"reject", "legal_hold_block", "immutable_block", "retention_block"}:
        return "warning"
    if action == "archive_check":
        status = str(detail.get("status", "")).lower()
        if status == "error":
            return "error"
        if status == "warning":
            return "warning"
        return "success"
    if action == "ocr" and detail.get("status") == "failed":
        return "error"
    if action == "processing_state" and detail.get("to") in {"ready", "sealed"}:
        return "success"
    if action in {"upload", "add_version", "approve", "immutable_set", "metadata_snapshot"}:
        return "success"
    return "info"


def _summary(action: str, detail: dict[str, Any], object_type: str) -> str:
    if action == "update" and isinstance(detail.get("changes"), dict):
        fields = ", ".join(_field_label(field) for field in detail["changes"].keys())
        return f"Geänderte Felder: {fields}" if fields else "Metadaten wurden geändert."
    if action == "processing_state":
        old = detail.get("from", "unknown")
        new = detail.get("to", "unknown")
        return f"{old} -> {new}"
    if action == "processing_failed":
        step = detail.get("step") or detail.get("failed_step")
        error = detail.get("error") or detail.get("message")
        if step and error:
            return f"{step}: {error}"
        if error:
            return str(error)
    if action == "ocr":
        pages = detail.get("pages")
        if pages is not None:
            return f"{pages} Seite(n) OCR verarbeitet."
    if action == "classify":
        rules = detail.get("rules")
        if isinstance(rules, list) and rules:
            return "Regeln: " + ", ".join(str(rule) for rule in rules)
    if action == "apply_suggestions":
        fields = detail.get("fields")
        if isinstance(fields, list) and fields:
            return "Übernommen: " + ", ".join(_field_label(str(field)) for field in fields)
    if action in {"upload", "delete"} and detail.get("title"):
        return str(detail["title"])
    if action == "add_version" and detail.get("version_no") is not None:
        return f"Version {detail['version_no']}"
    if action == "archive_check":
        errors = detail.get("errors")
        warnings = detail.get("warnings")
        if errors:
            return "Fehler: " + _join_values(errors)
        if warnings:
            return "Warnungen: " + _join_values(warnings)
        return "Archivprüfung ohne Auffälligkeiten."
    if action == "workflow" and detail.get("workflow"):
        return str(detail["workflow"])
    if action == "revision_package_export":
        return "Nachweispaket wurde erstellt."
    if object_type == "DocumentVersion":
        return "Ereignis an einer Dokumentversion."
    return ""


def _join_values(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _humanize(value: str) -> str:
    return value.replace("_", " ").strip().capitalize()


def _field_label(value: str) -> str:
    return FIELD_LABELS.get(value, _humanize(value))
