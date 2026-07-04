"""Workflow-Engine (paperless-artig): Trigger → Bedingungen → Aktionen.

Ergänzt die einfache ``ClassificationRule``-Engine (classification.py) um
mehrstufige, geordnete Workflows. Läuft in der Pipeline **nach** ``apply_rules``
(document_added), sodass Trigger auch auf regel-gesetzte Metadaten/Tags matchen.

Design (deterministisch, erklärbar – wie classification.py):
  * nur ``enabled`` Workflows, in ``order``-Reihenfolge;
  * ein Workflow feuert, wenn **einer** seiner Trigger des passenden Typs matcht
    (ODER zwischen Triggern, UND innerhalb eines Triggers);
  * Aktionen laufen in ``order``: Einzelwerte (Typ/Korrespondent/Ablage/Owner)
    werden **nur gesetzt, wenn noch leer**, Tags werden **akkumuliert**, remove
    entfernt Tags, Zusatzfeld-Werte werden gesetzt, der Titel wird gerendert;
  * pro gefeuertem Workflow ein ``AuditLogEntry(action="workflow")``.

Die Textbedingungen (``text_contains``/``text_regex``) verwenden bewusst denselben
``classification.rule_matches``-Helper wieder – über einen kleinen Adapter, der
ein ``match``-Dict trägt.
"""
from __future__ import annotations

import fnmatch
import logging
import os
from types import SimpleNamespace

from .classification import _searchable_text, rule_matches

logger = logging.getLogger(__name__)


def _trigger_matches(trigger, document, source: str, text: str) -> bool:
    """UND-verknüpfte Bedingungsprüfung eines einzelnen Triggers."""
    # Quelle: Schnittmenge (leere Liste = jede Quelle)
    sources = trigger.source or []
    if sources and source not in sources:
        return False

    # Pfad-Glob gegen den Dateipfad der aktuellen Version (voll oder Basename)
    if trigger.filter_path:
        version = document.current_version
        file_path = (version.file_path if version else "") or ""
        if not (
            fnmatch.fnmatch(file_path, trigger.filter_path)
            or fnmatch.fnmatch(os.path.basename(file_path), trigger.filter_path)
        ):
            return False

    # FK-Filter
    if (
        trigger.filter_correspondent_id
        and document.correspondent_id != trigger.filter_correspondent_id
    ):
        return False
    if (
        trigger.filter_document_type_id
        and document.document_type_id != trigger.filter_document_type_id
    ):
        return False

    # Tag-Filter: has_tags (alle vorhanden), has_not_tags (keiner vorhanden)
    has_tag_ids = None
    required = set(trigger.filter_has_tags.values_list("id", flat=True))
    if required:
        has_tag_ids = set(document.tags.values_list("id", flat=True))
        if not required.issubset(has_tag_ids):
            return False
    forbidden = set(trigger.filter_has_not_tags.values_list("id", flat=True))
    if forbidden:
        if has_tag_ids is None:
            has_tag_ids = set(document.tags.values_list("id", flat=True))
        if forbidden & has_tag_ids:
            return False

    # Textbedingung über den wiederverwendeten rule_matches-Helper
    match: dict = {}
    if trigger.text_contains:
        match["text_contains"] = trigger.text_contains
    if trigger.text_regex:
        match["text_regex"] = trigger.text_regex
    if match and not rule_matches(SimpleNamespace(match=match), text):
        return False

    return True


def _workflow_fires(workflow, document, trigger_type: str, source: str, text: str) -> bool:
    """True, wenn mindestens ein Trigger des Typs passt (ODER zwischen Triggern)."""
    for trigger in workflow.triggers.filter(trigger_type=trigger_type):
        if _trigger_matches(trigger, document, source, text):
            return True
    return False


def _render_title(template: str, document) -> str:
    """Rendert das Titel-Template mit {correspondent}, {created}, {doc_type}."""
    correspondent = document.correspondent.name if document.correspondent_id else ""
    doc_type = document.document_type.name if document.document_type_id else ""
    created = ""
    stamp = document.created_at or document.added_at
    if stamp:
        created = stamp.strftime("%Y-%m-%d")
    try:
        return template.format(
            correspondent=correspondent, created=created, doc_type=doc_type
        )
    except (KeyError, IndexError, ValueError):
        # Unbekannter Platzhalter → Template unverändert übernehmen (robust).
        return template


def _apply_actions(workflow, document) -> dict:
    """Wendet die Aktionen eines gefeuerten Workflows an; liefert ``applied``."""
    from .models import CustomField, CustomFieldValue

    applied: dict = {}

    for action in workflow.actions.all():
        if action.action_type == "assign":
            if action.document_type_id and document.document_type_id is None:
                document.document_type_id = action.document_type_id
                applied["document_type"] = action.document_type.name
            if action.correspondent_id and document.correspondent_id is None:
                document.correspondent_id = action.correspondent_id
                applied["correspondent"] = action.correspondent.name
            if action.storage_path_id and document.storage_path_id is None:
                document.storage_path_id = action.storage_path_id
                applied["storage_path"] = action.storage_path.name
            if action.owner_id and document.owner_id is None:
                document.owner_id = action.owner_id
                applied["owner"] = action.owner.get_username()

            for tag in action.tags.all():
                document.tags.add(tag)
                applied.setdefault("tags", [])
                if tag.name not in applied["tags"]:
                    applied["tags"].append(tag.name)

            for fname, value in (action.custom_fields or {}).items():
                field = CustomField.objects.filter(name=fname).first()
                if field is None:
                    continue
                CustomFieldValue.objects.update_or_create(
                    document=document, field=field, defaults={"value": str(value)}
                )
                applied.setdefault("custom_fields", {})[fname] = str(value)

            if action.title:
                rendered = _render_title(action.title, document)
                document.title = rendered
                applied["title"] = rendered

        elif action.action_type == "remove":
            for tag in action.tags.all():
                document.tags.remove(tag)
                applied.setdefault("removed_tags", [])
                if tag.name not in applied["removed_tags"]:
                    applied["removed_tags"].append(tag.name)

    document.save()
    return applied


def run_workflows(document, *, trigger_type: str, source: str, text: str | None = None) -> dict:
    """Führt alle passenden Workflows deterministisch aus.

    ``source`` ist die Herkunft der Version (upload/consume/mail/api). ``text``
    kann übergeben werden, um den durchsuchbaren Text nicht neu zu berechnen;
    sonst wird er wie bei ``apply_rules`` aus Titel + OCR-Text gebildet.
    """
    from .models import AuditLogEntry, Workflow

    if text is None:
        text = _searchable_text(document)

    fired: list[str] = []
    for workflow in Workflow.objects.filter(enabled=True).order_by("order", "name"):
        if not _workflow_fires(workflow, document, trigger_type, source, text):
            continue
        applied = _apply_actions(workflow, document)
        fired.append(workflow.name)
        AuditLogEntry.objects.create(
            action="workflow",
            object_type="Document",
            object_id=str(document.id),
            detail={"workflow": workflow.name, "applied": applied},
        )

    return {"workflows": fired}
