"""Workflow-Engine (STOAA-263): Trigger → Bedingungen → Aktionen.

run_workflows(document, *, trigger_type, source, text=None) ist der einzige
öffentliche Einstiegspunkt. Er läuft deterministisch in order-Reihenfolge
über alle enabled Workflows, prüft Trigger + Bedingungen und wendet passende
Aktionen an.

ClassificationRule bleibt unberührt; Workflows sind das mächtigere Konstrukt.
"""
from __future__ import annotations

import fnmatch
import logging

logger = logging.getLogger(__name__)


def _trigger_matches(trigger, *, source: str, document, text: str) -> bool:
    """Prüft alle Trigger-Bedingungen; True = feuern."""
    # Quell-Filter
    if trigger.sources:
        allowed = {s.strip() for s in trigger.sources.split(",") if s.strip()}
        if allowed and source not in allowed:
            return False

    # Pfad-Glob
    if trigger.filter_path:
        version = document.current_version
        path = (version.file_path or "") if version else ""
        if not fnmatch.fnmatch(path, trigger.filter_path):
            return False

    # Korrespondent
    if trigger.filter_correspondent_id is not None:
        if document.correspondent_id != trigger.filter_correspondent_id:
            return False

    # Dokumenttyp
    if trigger.filter_document_type_id is not None:
        if document.document_type_id != trigger.filter_document_type_id:
            return False

    # Tags: muss alle haben
    has_ids = set(trigger.filter_has_tags.values_list("id", flat=True))
    if has_ids:
        doc_ids = set(document.tags.values_list("id", flat=True))
        if not has_ids.issubset(doc_ids):
            return False

    # Tags: darf keinen haben
    has_not_ids = set(trigger.filter_has_not_tags.values_list("id", flat=True))
    if has_not_ids:
        doc_ids = set(document.tags.values_list("id", flat=True))
        if has_not_ids & doc_ids:
            return False

    # Textbedingungen via rule_matches-Logik (wiederverwendet)
    if trigger.filter_text_contains or trigger.filter_text_regex:
        from .classification import rule_matches

        class _FakeRule:
            match = {}

        fake = _FakeRule()
        if trigger.filter_text_contains:
            fake.match["text_contains"] = trigger.filter_text_contains
        if trigger.filter_text_regex:
            fake.match["text_regex"] = trigger.filter_text_regex
        if not rule_matches(fake, text):
            return False

    return True


def _apply_action(action, document) -> dict:
    """Führt eine einzelne Aktion aus; gibt dict der geänderten Felder zurück."""
    from .models import CustomField, CustomFieldValue

    changed: dict = {}

    if action.action_type == "assign":
        if action.assign_title:
            corr_name = document.correspondent.name if document.correspondent else ""
            dt_name = document.document_type.name if document.document_type else ""
            created = document.created_at.strftime("%Y-%m-%d") if document.created_at else ""
            new_title = action.assign_title.format(
                correspondent=corr_name,
                created=created,
                doc_type=dt_name,
            )
            document.title = new_title
            changed["title"] = new_title

        if action.assign_correspondent_id and document.correspondent_id is None:
            document.correspondent_id = action.assign_correspondent_id
            changed["correspondent"] = action.assign_correspondent_id

        if action.assign_document_type_id and document.document_type_id is None:
            document.document_type_id = action.assign_document_type_id
            changed["document_type"] = action.assign_document_type_id

        if action.assign_storage_path_id and document.storage_path_id is None:
            document.storage_path_id = action.assign_storage_path_id
            changed["storage_path"] = action.assign_storage_path_id

        if action.assign_owner_id and document.owner_id is None:
            document.owner_id = action.assign_owner_id
            changed["owner"] = action.assign_owner_id

        for tag in action.assign_tags.all():
            document.tags.add(tag)
            changed.setdefault("tags_added", []).append(tag.name)

        for field_id_str, value in (action.assign_custom_fields or {}).items():
            try:
                field = CustomField.objects.get(pk=int(field_id_str))
            except (CustomField.DoesNotExist, ValueError):
                continue
            CustomFieldValue.objects.update_or_create(
                document=document, field=field,
                defaults={"value": value},
            )
            changed.setdefault("custom_fields", {})[field.name] = value

    elif action.action_type == "remove":
        for tag in action.remove_tags.all():
            document.tags.remove(tag)
            changed.setdefault("tags_removed", []).append(tag.name)

    return changed


def run_workflows(document, *, trigger_type: str, source: str, text: str | None = None) -> dict:
    """Führt alle passenden, enabled Workflows in order-Reihenfolge aus.

    trigger_type: "document_added" | "document_updated"
    source:       "upload" | "consume" | "mail" | "api"
    text:         OCR-Text (optional, wird aus document ermittelt wenn None)
    """
    from .models import AuditLogEntry, Workflow

    if text is None:
        version = document.current_version
        text = (version.ocr_text or "") if version else ""
    text_lower = text.lower()

    fired: list[str] = []

    workflows = (
        Workflow.objects.filter(enabled=True)
        .prefetch_related(
            "trigger",
            "trigger__filter_has_tags",
            "trigger__filter_has_not_tags",
            "actions",
            "actions__assign_tags",
            "actions__remove_tags",
        )
        .order_by("order", "name")
    )

    for wf in workflows:
        try:
            trigger = wf.trigger
        except Workflow.trigger.RelatedObjectDoesNotExist:
            continue

        if trigger.trigger_type != trigger_type:
            continue

        if not _trigger_matches(trigger, source=source, document=document, text=text_lower):
            continue

        # Alle Aktionen in Reihenfolge anwenden
        applied: dict = {}
        for action in wf.actions.order_by("order"):
            try:
                changed = _apply_action(action, document)
                applied.update(changed)
            except Exception:
                logger.exception("Workflow-Aktion %s fehlgeschlagen (Workflow: %s)", action.pk, wf.name)

        # Dokument speichern (Tags sind schon via M2M direkt gesetzt)
        update_fields = []
        for field in ("title", "correspondent_id", "document_type_id", "storage_path_id", "owner_id"):
            if field.rstrip("_id") in applied or field in applied:
                update_fields.append(field)
        if update_fields or {"title", "correspondent_id", "document_type_id", "storage_path_id", "owner_id"} & set(applied):
            document.save()

        AuditLogEntry.objects.create(
            action="workflow",
            object_type="Document",
            object_id=str(document.id),
            detail={"workflow": wf.name, "applied": applied},
        )
        fired.append(wf.name)
        logger.info("Workflow %r auf Dokument %s angewandt (%s)", wf.name, document.id, applied)

    return {"workflows": fired}
