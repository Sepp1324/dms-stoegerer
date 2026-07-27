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

from django.db import transaction

from documents.models import (
    AuditLogEntry,
    Correspondent,
    Document,
    DocumentFolder,
    DocumentReminder,
    DocumentType,
    Tag,
)

logger = logging.getLogger(__name__)

_AGENT_SYSTEM = (
    "Du bist ein Assistent für ein Dokumenten-Management-System. Du planst nur "
    "erlaubte Aktionen und antwortest ausschließlich mit JSON. Verwende ausschließlich "
    "die vorgegebenen Dokument-IDs und Aktionstypen."
)


def _do_add_tag(user, document, params):
    name = str(params.get("tag", "")).strip()
    if not name:
        raise ValueError("Tag-Name fehlt.")
    tag = Tag.objects.filter(name__iexact=name).first() or Tag.objects.create(name=name[:64])
    was_present = document.tags.filter(pk=tag.pk).exists()
    document.tags.add(tag)
    return f"Tag '{tag.name}' hinzugefügt", {"tag_id": tag.id, "was_present": was_present}


def _do_set_note(user, document, params):
    note = str(params.get("note", "")).strip()
    if not note:
        raise ValueError("Notiz-Text fehlt.")
    previous = document.note
    applied = note[:5000]
    document.note = applied
    document.save(update_fields=["note"])
    # ``applied_note`` = der vom Agenten gesetzte Wert. Der Undo darf nur greifen,
    # wenn die Notiz DIESEM Wert noch entspricht (sonst überschriebe er eine
    # zwischenzeitliche Nutzeränderung).
    return "Notiz gesetzt", {"previous_note": previous, "applied_note": applied}


def _do_set_reminder(user, document, params):
    raw = str(params.get("date", "")).strip()
    try:
        due = date.fromisoformat(raw)
    except (ValueError, TypeError):
        raise ValueError(f"Ungültiges Datum '{raw}' (erwartet YYYY-MM-DD).")
    reminder = DocumentReminder.objects.create(
        document=document,
        remind_on=due,
        note=str(params.get("note", "")).strip(),
        created_by=user,
    )
    return f"Wiedervorlage am {due.isoformat()} angelegt", {"reminder_id": reminder.id}


def _get_or_create_ci(model, name: str):
    return model.objects.filter(name__iexact=name).first() or model.objects.create(
        name=name[:255]
    )


def _do_set_correspondent(user, document, params):
    name = str(params.get("name", "")).strip()
    if not name:
        raise ValueError("Korrespondent-Name fehlt.")
    previous = document.correspondent_id
    document.correspondent = _get_or_create_ci(Correspondent, name)
    document.save(update_fields=["correspondent"])
    return f"Korrespondent '{document.correspondent.name}' gesetzt", {
        "previous": previous,
        "applied": document.correspondent_id,
    }


def _do_set_document_type(user, document, params):
    name = str(params.get("name", "")).strip()
    if not name:
        raise ValueError("Dokumenttyp-Name fehlt.")
    previous = document.document_type_id
    document.document_type = _get_or_create_ci(DocumentType, name)
    document.save(update_fields=["document_type"])
    return f"Dokumenttyp '{document.document_type.name}' gesetzt", {
        "previous": previous,
        "applied": document.document_type_id,
    }


def _do_move_to_folder(user, document, params):
    name = str(params.get("folder", "")).strip()
    if not name:
        raise ValueError("Ordnername fehlt.")
    # Bewusst NUR bestehende Ordner (keine Auto-Anlage → keine Tippfehler-Ordner);
    # gleichnamige Ordner in verschiedenen Ebenen sind mehrdeutig → Fehler.
    # Owner-Scope (P1): NUR Ordner des Dokument-Eigentümers – sonst könnte der Agent
    # ein Dokument in einen eindeutig benannten FREMDEN Ordner verschieben und damit
    # den Serializer-/Bulk-Owner-Schutz umgehen. Zugleich löst das die neue
    # Namensmehrdeutigkeit (Root-Namen dürfen pro Owner doppelt vorkommen) auf.
    matches = list(
        DocumentFolder.objects.filter(
            name__iexact=name, owner_id=document.owner_id
        )[:2]
    )
    if not matches:
        raise ValueError(f"Ordner '{name}' nicht gefunden.")
    if len(matches) > 1:
        raise ValueError(f"Ordnername '{name}' ist mehrdeutig.")
    previous = document.folder_id
    document.folder = matches[0]
    document.save(update_fields=["folder"])
    return f"In Ordner '{matches[0].full_path}' verschoben", {
        "previous": previous,
        "applied": document.folder_id,
    }


HANDLERS = {
    "add_tag": _do_add_tag,
    "set_note": _do_set_note,
    "set_reminder": _do_set_reminder,
    "set_correspondent": _do_set_correspondent,
    "set_document_type": _do_set_document_type,
    "move_to_folder": _do_move_to_folder,
}


# --- Undo -------------------------------------------------------------------
# Jede Aktion hinterlegt beim Ausführen die zur Umkehr nötige Information im
# Audit-Eintrag ("undo"), inklusive des ANGEWANDTEN Werts. Ein Undo-Handler gibt
# ``(did_revert, message)`` zurück: ``did_revert=False`` bedeutet, dass NICHTS
# geändert wurde (z. B. weil der aktuelle Wert nicht mehr dem vom Agenten
# gesetzten entspricht – eine zwischenzeitliche Nutzeränderung darf NICHT
# überschrieben werden, P1).


def _undo_add_tag(document, undo) -> tuple[bool, str]:
    if undo.get("was_present"):
        return False, "Tag war vorher bereits gesetzt – nichts entfernt"
    document.tags.remove(undo.get("tag_id"))
    return True, "Tag entfernt"


def _undo_set_note(document, undo) -> tuple[bool, str]:
    # Nur zurücksetzen, wenn die Notiz noch dem vom Agenten gesetzten Wert
    # entspricht (Alt-Einträge ohne "applied_note" verhalten sich wie bisher).
    if "applied_note" in undo and (document.note or "") != (undo.get("applied_note") or ""):
        return False, "Notiz inzwischen geändert – nicht zurückgesetzt"
    document.note = undo.get("previous_note", "") or ""
    document.save(update_fields=["note"])
    return True, "Notiz zurückgesetzt"


def _undo_set_reminder(document, undo) -> tuple[bool, str]:
    deleted, _ = DocumentReminder.objects.filter(
        id=undo.get("reminder_id"), document=document
    ).delete()
    if not deleted:
        return False, "Wiedervorlage bereits entfernt – nichts zu tun"
    return True, "Wiedervorlage gelöscht"


def _undo_fk(document, undo, field: str, label: str) -> tuple[bool, str]:
    # Nur zurücksetzen, wenn das Feld noch den vom Agenten gesetzten Wert trägt
    # (sonst überschriebe der Undo eine zwischenzeitliche Nutzeränderung).
    if "applied" in undo and getattr(document, f"{field}_id") != undo.get("applied"):
        return False, f"{label} inzwischen geändert – nicht zurückgesetzt"
    setattr(document, f"{field}_id", undo.get("previous"))
    document.save(update_fields=[field])
    return True, f"{label} zurückgesetzt"


def _undo_move_to_folder(document, undo) -> tuple[bool, str]:
    """Setzt den Ordner zurück – aber NUR, wenn das Ziel weiterhin zulässig ist
    UND der aktuelle Ordner noch der vom Agenten gesetzte ist.

    Nutzeränderung (P1): Hat der Nutzer das Dokument nach der Agent-Aktion selbst
    verschoben, darf der Undo das nicht überschreiben. Owner-Scope (P1): ``None``
    (aus dem Ordner nehmen) ist immer erlaubt; ein konkreter Vorgänger nur, wenn er
    noch existiert und dem Dokument-Eigentümer gehört."""
    if "applied" in undo and document.folder_id != undo.get("applied"):
        return False, "Ordner inzwischen geändert – nicht zurückgesetzt"
    previous = undo.get("previous")
    if previous is not None and not DocumentFolder.objects.filter(
        pk=previous, owner_id=document.owner_id
    ).exists():
        return False, "Vorheriger Ordner nicht mehr zulässig – Zuordnung unverändert"
    document.folder_id = previous
    document.save(update_fields=["folder"])
    return True, "Ordner zurückgesetzt"


UNDO_HANDLERS = {
    "add_tag": _undo_add_tag,
    "set_note": _undo_set_note,
    "set_reminder": _undo_set_reminder,
    "set_correspondent": lambda d, u: _undo_fk(d, u, "correspondent", "Korrespondent"),
    "set_document_type": lambda d, u: _undo_fk(d, u, "document_type", "Dokumenttyp"),
    "move_to_folder": _undo_move_to_folder,
}


def undo(user, audit_id) -> dict:
    """Macht eine zuvor ausgeführte Agent-Aktion rückgängig (owner-gescoped).

    Atomar (P1): Umkehr, das Markieren des Ursprungs-Audits als ``undone`` und der
    ``agent_undo``-Audit laufen in EINER Transaktion unter ``select_for_update()``
    auf dem Ursprungs-Audit – ein Prozess-/DB-Fehler hinterlässt sonst eine Umkehr
    ohne Kennzeichnung (oder umgekehrt), und paralleles Undo würde doppelt umkehren.
    Wird nichts umgekehrt (aktueller Wert ≠ angewandter Wert oder Objekt bereits
    weg), bleibt der Eintrag ``undone=False`` und es entsteht KEIN ``agent_undo``.
    """
    with transaction.atomic():
        entry = (
            AuditLogEntry.objects.select_for_update()
            .filter(id=audit_id, actor=user, object_type="Document")
            .first()
        )
        if entry is None or not str(entry.action).startswith("agent_"):
            return {"status": "not_found", "message": "Aktion nicht gefunden."}
        action = str(entry.action)[len("agent_") :]
        if action not in UNDO_HANDLERS:
            return {"status": "unsupported", "message": "Nicht rückgängig machbar."}
        detail = dict(entry.detail or {})
        if detail.get("undone"):
            return {"status": "already_undone", "message": "Bereits rückgängig gemacht."}
        document = Document.objects.filter(id=entry.object_id, owner=user).first()
        if document is None:
            return {"status": "not_found", "message": "Dokument nicht gefunden oder kein Zugriff."}

        did_revert, message = UNDO_HANDLERS[action](document, detail.get("undo") or {})
        if not did_revert:
            # Nichts geändert (z. B. zwischenzeitliche Nutzeränderung) – NICHT als
            # undone markieren, kein agent_undo-Audit.
            return {"status": "skipped", "message": message}

        detail["undone"] = True
        entry.detail = detail
        entry.save(update_fields=["detail"])
        AuditLogEntry.objects.create(
            actor=user,
            action="agent_undo",
            object_type="Document",
            object_id=str(document.id),
            detail={"undid_audit": entry.id, "undid_action": action},
        )
    return {"status": "ok", "message": message}


def _summarize(action: str, params: dict, title: str) -> str:
    if action == "add_tag":
        return f"Tag '{params.get('tag', '')}' zu '{title}' hinzufügen"
    if action == "set_note":
        return f"Notiz an '{title}' setzen: {str(params.get('note', ''))[:80]}"
    if action == "set_reminder":
        return f"Wiedervorlage am {params.get('date', '?')} für '{title}' anlegen"
    if action == "set_correspondent":
        return f"Korrespondent '{params.get('name', '')}' an '{title}' setzen"
    if action == "set_document_type":
        return f"Dokumenttyp '{params.get('name', '')}' an '{title}' setzen"
    if action == "move_to_folder":
        return f"'{title}' in Ordner '{params.get('folder', '')}' verschieben"
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
        "- set_reminder: params {\"date\": \"YYYY-MM-DD\", \"note\": \"...\"}\n"
        "- set_correspondent: params {\"name\": \"...\"}\n"
        "- set_document_type: params {\"name\": \"...\"}\n"
        "- move_to_folder: params {\"folder\": \"...\"}  (nur bestehende Ordner)\n\n"
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
            # Mutation + Audit atomar (P1): sonst bliebe bei einem Fehler zwischen
            # Änderung und Audit eine Änderung OHNE Protokolleintrag bestehen.
            with transaction.atomic():
                summary, undo_payload = HANDLERS[action](user, document, params)
                entry = AuditLogEntry.objects.create(
                    actor=user,
                    action=f"agent_{action}",
                    object_type="Document",
                    object_id=str(doc_id),
                    # "undo" trägt die zur Umkehr nötige Information (Vorzustand +
                    # angewandter Wert bzw. angelegte Objekt-ID) – Grundlage für undo().
                    detail={"params": params, "undo": undo_payload},
                )
        except ValueError as exc:
            errors.append({"document": doc_id, "action": action, "error": str(exc)})
            continue
        applied.append(
            {
                "document": doc_id,
                "action": action,
                "summary": summary,
                "audit_id": entry.id,
            }
        )
    return {"applied": applied, "errors": errors}
