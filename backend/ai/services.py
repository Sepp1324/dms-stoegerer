"""Fachliche KI-Dienste des DMS.

Baut auf der Provider-Abstraktion auf und liefert *Vorschläge* – niemals
bindende Zuweisungen. Die regelbasierte Klassifizierung (deterministisch)
bleibt die primäre Quelle; KI ergänzt sie transparent (siehe KONZEPT.md §6).
"""
from __future__ import annotations

import json

from .providers import get_provider

_CLASSIFY_SYSTEM = (
    "Du bist ein Assistent für ein Dokumenten-Management-System. "
    "Analysiere den OCR-Text eines Dokuments und schlage Metadaten vor. "
    "Antworte ausschließlich mit einem JSON-Objekt mit den Schlüsseln: "
    "title (str), document_type (str), correspondent (str), "
    "tags (Liste von str), summary (str, max 2 Sätze). "
    "Wenn ein Wert unbekannt ist, gib einen leeren String bzw. eine leere Liste zurück."
)


def suggest_metadata(ocr_text: str, *, max_chars: int = 6000) -> dict:
    """Schlägt Metadaten zu einem Dokument vor (Titel, Typ, Korrespondent, Tags).

    Gibt ein Dict mit Vorschlägen zurück und markiert die Quelle als 'ai'.
    Bei deaktivierter/fehlender KI wird ein leerer Vorschlag zurückgegeben.
    """
    provider = get_provider()
    if not provider.available:
        return {"source": "unavailable", "suggestions": {}}

    excerpt = ocr_text[:max_chars]
    prompt = f"Hier ist der OCR-Text des Dokuments:\n\n{excerpt}"
    raw = provider.complete(prompt, system=_CLASSIFY_SYSTEM)

    suggestions = _parse_json(raw)
    return {"source": "ai", "provider": provider.name, "suggestions": suggestions}


def _parse_json(raw: str) -> dict:
    """Extrahiert das erste JSON-Objekt aus einer Modellantwort (robust)."""
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
