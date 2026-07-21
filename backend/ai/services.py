"""Fachliche KI-Dienste des DMS.

Baut auf der Provider-Abstraktion auf und liefert *Vorschläge* – niemals
bindende Zuweisungen. Die regelbasierte Klassifizierung (deterministisch)
bleibt die primäre Quelle; KI ergänzt sie transparent (siehe KONZEPT.md §6).
"""
from __future__ import annotations

import json
import logging
import re
from html import escape

from celery.exceptions import SoftTimeLimitExceeded

from . import flashcards
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
    except SoftTimeLimitExceeded:
        raise  # Soft-Time-Limit nie als {"source":"error"} tarnen (Task muss abbrechen)
    except Exception as exc:  # noqa: BLE001 – Provider-Fehler sprechend surfacen statt 500
        # Provider konfiguriert, aber Aufruf schlägt fehl (falscher/abgelaufener
        # Key, falsches Modell, Netzwerk, Rate-Limit). WARN loggen, damit Ops die
        # eigentliche Ursache im Pod-Log sieht (die UI zeigt nur eine generische
        # Meldung); klar von "unavailable" unterschieden.
        logger.warning(
            "KI-Generierung fehlgeschlagen (Provider %s): %s", provider.name, exc
        )
        return {"source": "error", "provider": provider.name, "error": str(exc)}

    suggestions = _parse_json(raw)
    return {"source": "ai", "provider": provider.name, "suggestions": suggestions}


def generate_flashcards(ocr_text: str, *, max_questions: int = 8) -> dict:
    """Erzeugt Psychologie-MC-Lernkarten aus Dokumenttext (für psychosr).

    Gibt ``{"source": "ai"|"unavailable"|"error", "questions": [...]}`` zurück.
    Jede Frage erfüllt den psychosr-Kontrakt (4 Aussagen, ≥1 richtig, kap 1..8).
    """
    provider = get_provider()
    if not provider.available:
        return {"source": "unavailable", "questions": []}
    prompt = flashcards.build_prompt(ocr_text, max_questions)
    try:
        raw = provider.complete(prompt, system=flashcards.SYSTEM, max_tokens=8192)
    except Exception as exc:  # noqa: BLE001 – Provider-Fehler sprechend surfacen
        logger.warning(
            "Flashcard-Generierung fehlgeschlagen (Provider %s): %s",
            provider.name,
            exc,
        )
        return {"source": "error", "provider": provider.name, "error": str(exc), "questions": []}
    questions = flashcards.parse_and_validate(raw, max_questions=max_questions)
    return {"source": "ai", "provider": provider.name, "questions": questions}


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


_ASK_SYSTEM = (
    "Du bist der Dokumenten-Copilot eines privaten DMS. "
    "Beantworte Fragen ausschließlich anhand der gelieferten Quellen. "
    "Wenn die Quellen keine belastbare Antwort enthalten, sage das klar. "
    "Nenne relevante Quellen im Text mit [S1], [S2] usw. "
    "Erfinde keine Fakten, Beträge, Fristen oder Namen."
)


def _query_terms(question: str) -> list[str]:
    """Extrahiert einfache Suchterme für ein robustes erstes Retrieval."""
    stopwords = {
        "aber",
        "alle",
        "auch",
        "auf",
        "aus",
        "bei",
        "bin",
        "bis",
        "das",
        "dem",
        "den",
        "der",
        "die",
        "ein",
        "eine",
        "einem",
        "einen",
        "für",
        "hat",
        "ich",
        "ist",
        "mit",
        "nach",
        "oder",
        "sich",
        "und",
        "von",
        "wann",
        "war",
        "was",
        "welche",
        "welchen",
        "wer",
        "wie",
        "wir",
        "zu",
        "zum",
        "zur",
    }
    terms = []
    for term in re.findall(r"[\wÄÖÜäöüß-]{3,}", question.lower()):
        if term not in stopwords and term not in terms:
            terms.append(term)
    return terms[:12]


def _snippet(text: str, terms: list[str], *, radius: int = 380) -> str:
    """Schneidet einen kompakten OCR-Ausschnitt um den besten Treffer."""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return ""
    lower = cleaned.lower()
    positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
    pos = min(positions) if positions else 0
    start = max(0, pos - radius // 2)
    end = min(len(cleaned), start + radius)
    start = max(0, end - radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(cleaned) else ""
    return f"{prefix}{cleaned[start:end]}{suffix}"


def _highlight(text: str, terms: list[str]) -> str:
    """Escaped Snippet mit <mark>-Treffern."""
    safe = escape(text)
    for term in sorted(terms, key=len, reverse=True):
        if not term:
            continue
        safe = re.sub(
            re.escape(escape(term)),
            lambda match: f"<mark>{match.group(0)}</mark>",
            safe,
            flags=re.IGNORECASE,
        )
    return safe


def _score_document(document, terms: list[str]) -> int:
    version = document.current_version
    text = " ".join(
        [
            document.title or "",
            document.correspondent.name if document.correspondent_id else "",
            document.document_type.name if document.document_type_id else "",
            document.folder.full_path if document.folder_id else "",
            version.ocr_text if version else "",
        ]
    ).lower()
    if not terms:
        return 1 if text.strip() else 0
    score = 0
    for term in terms:
        if term in text:
            score += 3
        score += min(text.count(term), 5)
    return score


def _score_text(text: str, terms: list[str]) -> int:
    lower = (text or "").lower()
    if not terms:
        return 1 if lower.strip() else 0
    score = 0
    for term in terms:
        if term in lower:
            score += 3
        score += min(lower.count(term), 5)
    return score


def retrieve_sources(question: str, documents_qs, *, limit: int = 6) -> list[dict]:
    """Findet zitierbare OCR-Quellen innerhalb eines bereits rechtlich gescopten QS."""
    terms = _query_terms(question)
    candidates = []
    for document in documents_qs:
        version = document.current_version
        if not version or not (version.ocr_text or "").strip():
            continue
        page_texts = list(version.page_texts.all())
        if page_texts:
            for page in page_texts:
                score = _score_text(
                    " ".join(
                        [
                            document.title or "",
                            document.correspondent.name if document.correspondent_id else "",
                            document.document_type.name if document.document_type_id else "",
                            document.folder.full_path if document.folder_id else "",
                            page.text,
                        ]
                    ),
                    terms,
                )
                if score > 0:
                    candidates.append((score, document, page.page_no, page.text))
            continue

        score = _score_document(document, terms)
        if score > 0:
            candidates.append((score, document, None, version.ocr_text))

    candidates.sort(key=lambda item: (item[0], item[1].added_at), reverse=True)
    sources = []
    seen: set[tuple[int, int | None]] = set()
    for _score, document, page_no, text in candidates:
        key = (document.id, page_no)
        if key in seen:
            continue
        seen.add(key)
        snippet = _snippet(text, terms)
        sources.append(
            {
                "id": f"S{len(sources) + 1}",
                "document": document.id,
                "document_title": document.title,
                "folder_path": document.folder.full_path if document.folder_id else None,
                "page": page_no,
                "snippet": snippet,
                "snippet_html": _highlight(snippet, terms),
            }
        )
        if len(sources) >= limit:
            break
    return sources


def answer_question(question: str, documents_qs, *, filters=None) -> dict:
    """Beantwortet eine Frage anhand sichtbarer Dokumentquellen.

    Retrieval passiert bewusst im DMS-Kernservice: dort werden OCR, Seitentexte,
    Metadaten, Entitäten, Verträge und Akten owner-gescoped zu belegbaren
    Source-Cards verdichtet. Die KI formuliert nur noch auf Basis dieser Karten.
    """
    from documents.services.retrieval import format_sources_for_prompt, retrieve_context

    retrieval = retrieve_context(question, documents_qs, filters=filters)
    sources = retrieval["sources"]
    if not sources:
        return {
            "source": "retrieval",
            "answer": "Ich habe in den sichtbaren Dokumenten keine passenden Quellen gefunden.",
            "sources": [],
            "retrieval": retrieval,
        }

    provider = get_provider()
    if not provider.available:
        return {
            "source": "unavailable",
            "answer": (
                "KI ist derzeit nicht verfügbar. Ich habe aber passende Quellen "
                "gefunden; öffne die Treffer unten für die manuelle Prüfung."
            ),
            "sources": sources,
            "retrieval": retrieval,
        }

    prompt = (
        f"Frage:\n{question.strip()}\n\n"
        f"Suchbegriffe: {', '.join(retrieval['query_terms']) or '-'}\n\n"
        f"Quellen:\n{format_sources_for_prompt(sources)}\n\n"
        "Antworte kurz und konkret auf Deutsch. Verwende Quellenmarker wie [S1]."
    )
    try:
        answer = provider.complete(prompt, system=_ASK_SYSTEM).strip()
    except Exception as exc:  # noqa: BLE001 – Providerfehler UI-freundlich abfangen
        logger.warning("Copilot-Antwort fehlgeschlagen (Provider %s): %s", provider.name, exc)
        return {
            "source": "error",
            "provider": provider.name,
            "answer": (
                "Die KI-Antwort konnte nicht erzeugt werden. Die gefundenen "
                "Quellen stehen unten zur manuellen Prüfung bereit."
            ),
            "sources": sources,
            "retrieval": retrieval,
            "error": str(exc),
        }

    return {
        "source": "ai",
        "provider": provider.name,
        "answer": answer,
        "sources": sources,
        "retrieval": retrieval,
    }
