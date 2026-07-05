"""Fachliche KI-Dienste des DMS.

Baut auf der Provider-Abstraktion auf und liefert *Vorschläge* – niemals
bindende Zuweisungen. Die regelbasierte Klassifizierung (deterministisch)
bleibt die primäre Quelle; KI ergänzt sie transparent (siehe KONZEPT.md §6).
"""
from __future__ import annotations

import json
import logging

from .providers import get_provider

logger = logging.getLogger(__name__)

_CLASSIFY_SYSTEM = (
    "Du bist ein Assistent für ein Dokumenten-Management-System. "
    "Analysiere den OCR-Text eines Dokuments und schlage Metadaten vor. "
    "Antworte ausschließlich mit einem JSON-Objekt mit den Schlüsseln: "
    "title (str), document_type (str), correspondent (str), "
    "date (str, ISO YYYY-MM-DD, Belegdatum; leer wenn unbekannt), "
    "tags (Liste von str), summary (str, max 2 Sätze). "
    "Wenn ein Wert unbekannt ist, gib einen leeren String bzw. eine leere Liste zurück."
)

# Obergrenze je Bestandswert-Liste im Prompt-Kontext (Token-Budget schonen).
_CONTEXT_CAP = 50


def _existing_context() -> str:
    """Baut einen Prompt-Zusatz aus vorhandenen Stammdaten (Bestandswerte).

    Reicht die vorhandenen Korrespondenten-, Dokumenttyp- und Tag-Namen an das
    Modell weiter, damit es bei Übereinstimmung exakt den Bestandsnamen nutzt
    (keine Varianten/Duplikate wie "Finanzamt" vs. "finanzamt"). Listen sind
    gekappt (``_CONTEXT_CAP``), um das Token-Budget zu begrenzen.

    Lazy-Import der Models, da ``ai`` sonst zyklisch von ``documents`` abhinge.
    """
    from django.db.models import Count

    from documents.models import Correspondent, DocumentType, Tag

    correspondents = list(
        Correspondent.objects.order_by("name").values_list("name", flat=True)[
            :_CONTEXT_CAP
        ]
    )
    doc_types = list(
        DocumentType.objects.order_by("name").values_list("name", flat=True)[
            :_CONTEXT_CAP
        ]
    )
    # Häufigste Tags zuerst (nach Dokument-Anzahl), damit die relevantesten
    # Bestandswerte im gekappten Budget landen.
    tags = list(
        Tag.objects.annotate(_n=Count("documents"))
        .order_by("-_n", "name")
        .values_list("name", flat=True)[:_CONTEXT_CAP]
    )

    blocks = []
    if correspondents:
        blocks.append("Vorhandene Korrespondenten: " + ", ".join(correspondents) + ".")
    if doc_types:
        blocks.append("Vorhandene Dokumenttypen: " + ", ".join(doc_types) + ".")
    if tags:
        blocks.append("Vorhandene Schlagworte: " + ", ".join(tags) + ".")
    if not blocks:
        return ""

    return (
        "\n\n"
        + " ".join(blocks)
        + " Wenn ein passender Bestandswert existiert, verwende exakt diesen "
        "Namen (keine Varianten, keine Duplikate)."
    )


def suggest_metadata(ocr_text: str, *, max_chars: int = 6000) -> dict:
    """Schlägt Metadaten zu einem Dokument vor (Titel, Typ, Korrespondent, Tags).

    Gibt ein Dict mit Vorschlägen zurück und markiert die Quelle als 'ai'.
    Bei deaktivierter/fehlender KI wird ein leerer Vorschlag zurückgegeben.
    """
    provider = get_provider()
    if not provider.available:
        return {"source": "unavailable", "suggestions": {}}

    system = _CLASSIFY_SYSTEM + _existing_context()
    excerpt = ocr_text[:max_chars]
    prompt = f"Hier ist der OCR-Text des Dokuments:\n\n{excerpt}"
    try:
        raw = provider.complete(prompt, system=system)
    except Exception as exc:  # noqa: BLE001 – Provider-Fehler sprechend surfacen statt 500
        # Provider konfiguriert, aber Aufruf schlägt fehl (falscher/abgelaufener
        # Key, falsches Modell, Netzwerk, Rate-Limit). Klar von "unavailable"
        # unterscheiden, damit die UI eine sprechende Meldung zeigt.
        logger.warning(
            "KI-Generierung fehlgeschlagen (Provider %s): %s", provider.name, exc
        )
        return {
            "source": "error",
            "provider": provider.name,
            "suggestions": {},
            "error": _short_error(exc),
        }

    suggestions = _parse_json(raw)
    return {"source": "ai", "provider": provider.name, "suggestions": suggestions}


def _short_error(exc: Exception) -> str:
    """Kurze, nutzerfreundliche Ursache – ohne Stacktrace/Secrets, längenbegrenzt."""
    name = type(exc).__name__
    text = str(exc).strip()
    first_line = text.splitlines()[0] if text else ""
    first_line = first_line[:160]
    return f"{name}: {first_line}" if first_line else name


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
