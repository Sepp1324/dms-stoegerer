"""Impact-Simulation fuer Klassifizierungsregeln.

Die Simulation ist read-only: Sie nutzt die gleiche Match-Logik wie die echte
Klassifizierung, schreibt aber keine Metadaten. Dadurch kann eine Regel vor dem
Aktivieren gegen sichtbare Dokumente geprueft werden.
"""
from __future__ import annotations

from documents import classification
from documents.models import Document
from documents.services.asn import format_asn


SIMULATION_SAMPLE_LIMIT = 25


class _RuleLike:
    def __init__(self, match: dict):
        self.match = match


def _document_text(document: Document) -> str:
    parts = [document.title or ""]
    if document.current_version and document.current_version.ocr_text:
        parts.append(document.current_version.ocr_text)
    return " ".join(parts).lower()


def _normalize_then(then: dict | None) -> dict:
    source = then if isinstance(then, dict) else {}
    result = {}
    for key in ("document_type", "correspondent", "storage_path", "folder"):
        value = str(source.get(key, "")).strip()
        if value:
            result[key] = value
    tags = [
        str(tag).strip()
        for tag in (source.get("tags") or [])
        if str(tag).strip()
    ]
    if tags:
        result["tags"] = tags
    return result


def _field_value(document: Document, field: str):
    if field == "document_type":
        return document.document_type.name if document.document_type_id else None
    if field == "correspondent":
        return document.correspondent.name if document.correspondent_id else None
    if field == "storage_path":
        return document.storage_path.name if document.storage_path_id else None
    if field == "folder":
        return document.folder.full_path if document.folder_id else None
    return None


def _impact_for_document(document: Document, then: dict) -> dict:
    would_change = []
    already_ok = []
    conflicts = []

    for field in ("document_type", "correspondent", "storage_path", "folder"):
        target = then.get(field)
        if not target:
            continue
        current = _field_value(document, field)
        if current is None:
            would_change.append({"field": field, "to": target})
        elif current == target:
            already_ok.append({"field": field, "value": target})
        else:
            conflicts.append({"field": field, "current": current, "to": target})

    target_tags = then.get("tags") or []
    if target_tags:
        current_tags = set(document.tags.values_list("name", flat=True))
        missing_tags = [tag for tag in target_tags if tag not in current_tags]
        existing_tags = [tag for tag in target_tags if tag in current_tags]
        if missing_tags:
            would_change.append({"field": "tags", "add": missing_tags})
        if existing_tags:
            already_ok.append({"field": "tags", "value": existing_tags})

    return {
        "would_change": would_change,
        "already_ok": already_ok,
        "conflicts": conflicts,
    }


def _risk(match_rate: float, conflicts: int, matched: int, would_update: int) -> str:
    if conflicts or match_rate >= 0.5 or matched > 50:
        return "high"
    if match_rate >= 0.2 or matched > 15 or would_update > 10:
        return "medium"
    return "low"


def _warnings(risk: str, *, match_rate: float, conflicts: int, matched: int) -> list[str]:
    warnings = []
    if risk == "high":
        warnings.append("Regel wirkt breit oder erzeugt Konflikte.")
    if match_rate >= 0.5:
        warnings.append("Mehr als die Haelfte der sichtbaren Dokumente wuerde matchen.")
    if conflicts:
        warnings.append(f"{conflicts} Treffer haben abweichende bestehende Metadaten.")
    if matched == 0:
        warnings.append("Keine Treffer: Regel waere aktuell wirkungslos.")
    return warnings


def simulate_rule(match: dict, then: dict, documents) -> dict:
    """Simuliert eine Regel gegen ein sichtbares Dokument-Queryset."""
    then = _normalize_then(then)
    rule = _RuleLike(match)
    total = documents.count()
    matched = 0
    would_update = 0
    already_ok = 0
    conflicts = 0
    samples = []

    for document in documents:
        if not classification.rule_matches(
            rule,
            _document_text(document),
            subject=document.mail_subject,
            sender=document.mail_sender,
        ):
            continue

        matched += 1
        impact = _impact_for_document(document, then)
        if impact["would_change"]:
            would_update += 1
        if impact["already_ok"] and not impact["would_change"] and not impact["conflicts"]:
            already_ok += 1
        if impact["conflicts"]:
            conflicts += 1

        if len(samples) < SIMULATION_SAMPLE_LIMIT:
            samples.append(
                {
                    "id": document.id,
                    "title": document.title,
                    "asn_label": format_asn(document.asn) if document.asn else None,
                    "correspondent_name": _field_value(document, "correspondent"),
                    "document_type_name": _field_value(document, "document_type"),
                    "folder_path": _field_value(document, "folder"),
                    **impact,
                }
            )

    match_rate = matched / total if total else 0
    risk = _risk(match_rate, conflicts, matched, would_update)
    impact_score = max(
        0,
        min(
            100,
            100
            - int(match_rate * 35)
            - conflicts * 12
            - max(0, would_update - 10) * 2,
        ),
    )
    return {
        "total_documents": total,
        "matched": matched,
        "would_update": would_update,
        "already_ok": already_ok,
        "conflicts": conflicts,
        "match_rate": round(match_rate, 4),
        "risk": risk,
        "impact_score": impact_score,
        "warnings": _warnings(
            risk,
            match_rate=match_rate,
            conflicts=conflicts,
            matched=matched,
        ),
        "sample_limit": SIMULATION_SAMPLE_LIMIT,
        "matches": samples,
    }
