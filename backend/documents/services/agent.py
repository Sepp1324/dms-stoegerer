"""Copilot-Agent: schlägt sichere Aktionen vor, führt sie nach Bestätigung aus.

Sicherheitsmodell:
- Die KI *schlägt* nur einen Plan aus einer eng begrenzten Whitelist vor
  (add_tag / set_note / set_reminder) und darf ausschließlich Dokument-IDs
  referenzieren, die zuvor per Suche als Kandidaten ermittelt wurden.
- Ausgeführt wird NUR nach expliziter Bestätigung durch den Nutzer, und der
  Execute-Pfad ist komplett deterministisch (kein LLM): jede Aktion wird gegen
  die Whitelist geprüft und strikt owner-gescoped angewandt (nur eigene
  Dokumente), mit Audit-Eintrag. Keine destruktiven Aktionen, kein Teilen.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from documents.models import AuditLogEntry, Document, DocumentReminder, Tag

logger = logging.getLogger(__name__)

_AGENT_SYSTEM = (
    "Du bist ein Assistent für ein Dokumenten-Management-System. Du planst nur "
    "erlaubte Aktionen und antwortest ausschließlich mit JSON. Verwende ausschließlich "
    "die vorgegebenen Dokument-IDs und Aktionstypen."
)


def _do_add_tag(user, document, params) -> str:
    name = str(params.get("tag", "")).strip()
    if not name:
        raise ValueError("Tag-Name fehlt.")
    tag = Tag.objects.filter(name__iexact=name).first() or Tag.objects.create(name=name[:64])
    document.tags.add(tag)
    return f"Tag '{tag.name}' hinzugefügt"


def _do_set_note(user, document, params) -> str:
    note = str(params.get("note", "")).strip()
    if not note:
        raise ValueError("Notiz-Text fehlt.")
    document.note = note[:5000]
    document.save(update_fields=["note"])
    return "Notiz gesetzt"


def _do_set_reminder(user, document, params) -> str:
    raw = str(params.get("date", "")).strip()
    try:
        due = date.fromisoformat(raw)
    except (ValueError, TypeError):
        raise ValueError(f"Ungültiges Datum '{raw}' (erwartet YYYY-MM-DD).")
    DocumentReminder.objects.create(
        document=document,
        remind_on=due,
        note=str(params.get("note", "")).strip(),
        created_by=user,
    )
    return f"Wiedervorlage am {due.isoformat()} angelegt"


HANDLERS = {
    "add_tag": _do_add_tag,
    "set_note": _do_set_note,
    "set_reminder": _do_set_reminder,
}


def _summarize(action: str, params: dict, title: str) -> str:
    if action == "add_tag":
        return f"Tag '{params.get('tag', '')}' zu '{title}' hinzufügen"
    if action == "set_note":
        return f"Notiz an '{title}' setzen: {str(params.get('note', ''))[:80]}"
    if action == "set_reminder":
        return f"Wiedervorlage am {params.get('date', '?')} für '{title}' anlegen"
    return action


def _parse_and_validate(raw: str, candidates: dict[int, str]) -> list[dict]:
    """Extrahiert die actions aus der LLM-Antwort und lässt nur Valides durch."""
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        logger.warning("Agent: LLM-Antwort nicht als JSON parsebar")
        return []

    out: list[dict] = []
    for item in data.get("actions", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        document = item.get("document")
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        if action not in HANDLERS:
            continue
        try:
            document = int(document)
        except (TypeError, ValueError):
            continue
        if document not in candidates:  # nur zuvor ermittelte Kandidaten
            continue
        out.append(
            {
                "action": action,
                "document": document,
                "document_title": candidates[document],
                "params": params,
                "summary": _summarize(action, params, candidates[document]),
            }
        )
    return out


def plan(user, instruction: str, *, limit: int = 10) -> dict:
    """Ermittelt Kandidaten-Dokumente und lässt die KI einen Aktionsplan vorschlagen.

    Führt NICHTS aus. Nur eigene Dokumente kommen als Ziele in Frage.
    """
    instruction = (instruction or "").strip()
    if len(instruction) < 3:
        return {"status": "invalid", "answer": "Bitte eine Anweisung eingeben.", "actions": []}

    from ai.providers import get_provider
    from documents.services import hybrid_search

    visible = Document.objects.filter(owner=user).exclude(current_version__isnull=True)
    hits = hybrid_search.hybrid_search(visible, instruction, limit=limit)
    candidates = {int(h["document"]): h["document_title"] for h in hits}

    if not candidates:
        return {
            "status": "no_candidates",
            "answer": "Keine passenden Dokumente zu dieser Anweisung gefunden.",
            "actions": [],
        }

    provider = get_provider()
    if not provider.available:
        return {
            "status": "unavailable",
            "answer": "KI ist derzeit nicht verfügbar (kein Provider konfiguriert).",
            "actions": [],
            "candidates": [{"document": d, "title": t} for d, t in candidates.items()],
        }

    doc_lines = "\n".join(f"- [{did}] {title}" for did, title in candidates.items())
    prompt = (
        f"Anweisung des Nutzers:\n{instruction}\n\n"
        f"Verfügbare Dokumente (verwende NUR diese IDs):\n{doc_lines}\n\n"
        "Erlaubte Aktionen:\n"
        "- add_tag: params {\"tag\": \"...\"}\n"
        "- set_note: params {\"note\": \"...\"}\n"
        "- set_reminder: params {\"date\": \"YYYY-MM-DD\", \"note\": \"...\"}\n\n"
        "Antworte AUSSCHLIESSLICH mit JSON in dieser Form:\n"
        '{"actions": [{"action": "add_tag", "document": <id>, "params": {"tag": "..."}}]}\n'
        "Schlage nur Aktionen vor, die zur Anweisung passen. Keine Erklärung, nur JSON."
    )
    try:
        raw = provider.complete(prompt, system=_AGENT_SYSTEM)
    except Exception:  # noqa: BLE001 – Providerfehler UI-freundlich abfangen
        logger.warning("Agent: Provider-Aufruf fehlgeschlagen", exc_info=True)
        return {"status": "error", "answer": "Die KI-Planung ist fehlgeschlagen.", "actions": []}

    actions = _parse_and_validate(raw, candidates)
    return {
        "status": "ok",
        "answer": f"{len(actions)} Aktion(en) vorgeschlagen."
        if actions
        else "Keine passenden Aktionen vorgeschlagen.",
        "actions": actions,
    }


def execute(user, actions) -> dict:
    """Führt bestätigte Aktionen deterministisch aus – strikt owner-gescoped."""
    applied: list[dict] = []
    errors: list[dict] = []
    for item in actions if isinstance(actions, list) else []:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        try:
            doc_id = int(item.get("document"))
        except (TypeError, ValueError):
            errors.append({"action": action, "error": "Ungültige Dokument-ID."})
            continue
        if action not in HANDLERS:
            errors.append({"document": doc_id, "action": action, "error": "Unbekannte Aktion."})
            continue
        document = Document.objects.filter(id=doc_id, owner=user).first()
        if document is None:
            errors.append(
                {"document": doc_id, "action": action, "error": "Dokument nicht gefunden oder kein Zugriff."}
            )
            continue
        try:
            summary = HANDLERS[action](user, document, params)
        except ValueError as exc:
            errors.append({"document": doc_id, "action": action, "error": str(exc)})
            continue
        AuditLogEntry.objects.create(
            actor=user,
            action=f"agent_{action}",
            object_type="Document",
            object_id=str(doc_id),
            detail={"params": params},
        )
        applied.append({"document": doc_id, "action": action, "summary": summary})
    return {"applied": applied, "errors": errors}
