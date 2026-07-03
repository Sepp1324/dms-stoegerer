"""Regelbasierte Klassifizierung (deterministisch, erklärbar).

Läuft nach dem OCR in der Pipeline. Anders als die KI (Vorschläge zum
Bestätigen) setzen Regeln Metadaten **direkt** – nachvollziehbar über ein
Audit-Log und das Feld ``Document.classification``.

Regel-Schema (``ClassificationRule``):
  match: {"text_contains": ["Rechnung", "Invoice"], "text_regex": "SR-\\d+"}
         – mehrere Bedingungen werden UND-verknüpft; eine Wortliste bei
           text_contains ist ODER-verknüpft (irgendeines enthalten).
  then:  {"document_type": "Rechnung", "correspondent": "Stadtwerke",
          "tags": ["Finanzen"], "storage_path": "Rechnungen"}
         – Einzelwerte (Typ/Korrespondent/Ablagepfad) werden nur gesetzt, wenn
           noch nicht belegt; Tags werden ergänzt.
"""
from __future__ import annotations

import re


def _searchable_text(document) -> str:
    parts = [document.title or ""]
    version = document.current_version
    if version and version.ocr_text:
        parts.append(version.ocr_text)
    return " ".join(parts).lower()


def rule_matches(rule, text: str) -> bool:
    match = rule.match or {}
    checks = []

    contains = match.get("text_contains")
    if contains:
        needles = [
            str(n).lower()
            for n in (contains if isinstance(contains, list) else [contains])
            if str(n).strip()
        ]
        checks.append(any(n in text for n in needles))

    regex = match.get("text_regex")
    if regex:
        try:
            checks.append(bool(re.search(regex, text, re.IGNORECASE)))
        except re.error:
            checks.append(False)

    # Ohne erkannte Bedingung greift die Regel nicht (verhindert Alles-Treffer).
    return bool(checks) and all(checks)


def apply_rules(document) -> dict:
    """Wendet alle passenden Regeln (nach Priorität) auf ein Dokument an."""
    from .models import (
        AuditLogEntry,
        ClassificationRule,
        Correspondent,
        DocumentType,
        StoragePath,
        Tag,
    )

    text = _searchable_text(document)
    matched: list[str] = []
    applied: dict = {}

    for rule in ClassificationRule.objects.filter(enabled=True).order_by("priority", "name"):
        if not rule_matches(rule, text):
            continue
        matched.append(rule.name)
        then = rule.then or {}

        dt = str(then.get("document_type", "")).strip()
        if dt and document.document_type is None:
            document.document_type, _ = DocumentType.objects.get_or_create(name=dt)
            applied["document_type"] = dt

        corr = str(then.get("correspondent", "")).strip()
        if corr and document.correspondent is None:
            document.correspondent, _ = Correspondent.objects.get_or_create(name=corr)
            applied["correspondent"] = corr

        sp = str(then.get("storage_path", "")).strip()
        if sp and document.storage_path is None:
            document.storage_path, _ = StoragePath.objects.get_or_create(
                name=sp,
                defaults={"path_template": "archive/{jahr}/{korrespondent}/{titel}"},
            )
            applied["storage_path"] = sp

        for tname in then.get("tags") or []:
            name = str(tname).strip()
            if name:
                tag, _ = Tag.objects.get_or_create(name=name, parent=None)
                document.tags.add(tag)
                applied.setdefault("tags", [])
                if name not in applied["tags"]:
                    applied["tags"].append(name)

    if matched:
        document.classification = {"rules": matched, "applied": applied}
        document.save()
        AuditLogEntry.objects.create(
            action="classify",
            object_type="Document",
            object_id=str(document.id),
            detail={"rules": matched, "applied": applied},
        )

    return {"rules": matched, "applied": applied}
