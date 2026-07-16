"""Erzeugung von Psychologie-MC-Lernkarten aus Dokumenttext.

Reines Modul (nur Standardbibliothek) – enthält Prompt-Bau und die robuste
Validierung der Modellantwort. Damit ist die Kernlogik ohne Django/Netzwerk
testbar. Der eigentliche Provider-Aufruf lebt in ``ai.services``.

Zielformat = das Aussagen-MC-Format von **psychosr** (`POST /api/mc/add`):
jede Frage hat GENAU 4 Aussagen, jede einzeln richtig/falsch, mindestens eine
richtig, und ist einem der 8 Aufnahmetest-Kapitel (``kap`` 1..8) zugeordnet.
"""
from __future__ import annotations

import json

# Muss 1:1 zu psychosr (CHAPTER_NAMES) passen – bestimmt das gültige ``kap``.
CHAPTER_NAMES = {
    1: "Was ist Psychologie?",
    2: "Geschichte der Psychologie",
    3: "Methodenlehre & Statistik",
    4: "Biologische Psychologie",
    5: "Allgemeine Psychologie (Wahrnehmung, Lernen, Gedächtnis)",
    6: "Entwicklungspsychologie",
    7: "Sozialpsychologie",
    8: "Differentielle & Persönlichkeitspsychologie",
}

SYSTEM = (
    "Du erstellst prüfungsnahe deutsche Multiple-Choice-Fragen (4 Aussagen, je "
    "richtig/falsch) für den österreichischen Psychologie-Aufnahmetest, basierend "
    "AUSSCHLIESSLICH auf dem gelieferten Dokumenttext. Ordne jede Frage einem der "
    "8 Kapitel zu. Antworte nur mit JSON."
)


def build_prompt(text: str, count: int, *, max_chars: int = 6000) -> str:
    excerpt = (text or "")[:max_chars]
    chapters = "\n".join(f"  {k} = {v}" for k, v in CHAPTER_NAMES.items())
    return json.dumps(
        {
            "task": (
                f"Erstelle bis zu {count} eigenständige Multiple-Choice-Fragen zum "
                "folgenden Dokument. Nutze nur Inhalte, die im Text belegt sind."
            ),
            "kapitel_zuordnung": (
                "Ordne jede Frage per 'kap' (1-8) dem thematisch passendsten "
                "Kapitel zu:\n" + chapters
            ),
            "format": {
                "questions": [
                    {
                        "frage": "Frage auf Deutsch",
                        "aussagen": [
                            {"text": "Aussage 1", "richtig": True},
                            {"text": "Aussage 2", "richtig": False},
                            {"text": "Aussage 3", "richtig": False},
                            {"text": "Aussage 4", "richtig": True},
                        ],
                        "erklaerung": "kurze deutsche Begründung",
                        "kap": 1,
                    }
                ]
            },
            "rules": [
                f"Höchstens {count} Fragen; lieber weniger, dafür fachlich korrekt.",
                "Alles auf Deutsch.",
                "Jede Frage hat GENAU 4 Aussagen, jede einzeln richtig oder falsch.",
                "Pro Frage 1-3 richtige Aussagen, mindestens eine falsche.",
                "'kap' ist eine ganze Zahl von 1 bis 8.",
                "Keine Aussage wie 'alle oben' oder 'keine davon'.",
                "Nichts erfinden, was nicht aus dem Dokument hervorgeht.",
            ],
            "dokument": excerpt,
        },
        ensure_ascii=False,
    )


def _clean_statement(item: object) -> dict | None:
    if not isinstance(item, dict) or "text" not in item or "richtig" not in item:
        return None
    text = str(item.get("text", "")).strip()
    if not text:
        return None
    return {"text": text, "richtig": bool(item.get("richtig"))}


def parse_and_validate(raw: object, *, max_questions: int = 8) -> list[dict]:
    """Modellantwort → geprüfte MC-Fragen (verwirft alles Ungültige).

    ``raw`` darf der rohe Antworttext (str), ein Dict mit ``questions`` oder
    direkt eine Liste sein. Jede zurückgegebene Frage erfüllt exakt den
    psychosr-Kontrakt: 4 Aussagen, ≥1 richtig, ``kap`` ∈ 1..8.
    """
    data: object = raw
    if isinstance(raw, str):
        s = raw.strip()
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end == -1:
            return []
        try:
            data = json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return []

    if isinstance(data, dict):
        questions = data.get("questions", [])
    elif isinstance(data, list):
        questions = data
    else:
        return []

    out: list[dict] = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        frage = str(q.get("frage", "")).strip()
        if not frage:
            continue
        raw_aus = q.get("aussagen")
        if not isinstance(raw_aus, list) or len(raw_aus) != 4:
            continue
        aussagen = [_clean_statement(a) for a in raw_aus]
        if any(a is None for a in aussagen):
            continue
        if not any(a["richtig"] for a in aussagen):
            continue
        try:
            kap = int(q.get("kap"))
        except (TypeError, ValueError):
            continue
        if kap not in CHAPTER_NAMES:
            continue
        out.append({"frage": frage, "aussagen": aussagen, "kap": kap})
        if len(out) >= max_questions:
            break
    return out
