"""Deterministische Extraktion strukturierter Metadaten für die Smart Inbox.

Die Smart Inbox soll Vorschläge machen, aber keine stillen Änderungen am
Dokument vornehmen. Deshalb erzeugt dieser Service ``ExtractionCandidate``-
Objekte mit Quelle, Snippet und Konfidenz; erst ein Nutzer übernimmt oder
verwirft sie über die API.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from documents.models import Document, ExtractionCandidate


DATE_RE = re.compile(
    r"\b(?P<day>[0-3]?\d)[./](?P<month>[01]?\d)[./](?P<year>19\d{2}|20\d{2})\b"
    r"|\b(?P<iso>19\d{2}-[01]\d-[0-3]\d)\b"
)
AMOUNT_RE = re.compile(
    r"(?:(?:EUR|€)\s*(?P<prefix>[0-9]{1,3}(?:[.\s][0-9]{3})*(?:,[0-9]{2})|[0-9]+(?:,[0-9]{2})?))"
    r"|(?:(?P<suffix>[0-9]{1,3}(?:[.\s][0-9]{3})*(?:,[0-9]{2})|[0-9]+(?:,[0-9]{2})?)\s*(?:EUR|€|Euro))",
    re.IGNORECASE,
)
IBAN_RE = re.compile(
    r"\b[A-Z]{2}\d{2}(?:[ \u00a0]?[A-Z0-9]){11,30}\b",
    re.IGNORECASE,
)
CONTRACT_RE = re.compile(
    r"\b(?:Vertrags(?:nummer|nr\.?)|Vertrag|Mandatsreferenz|Kundennummer)\b"
    r"\s*[:#]?\s*(?P<value>[A-Z0-9][A-Z0-9/.\-]{3,40})",
    re.IGNORECASE,
)
POLICY_RE = re.compile(
    r"\b(?:Polizzennummer|Polizzen?nr\.?|Polizze|Versicherungsnummer)\b"
    r"\s*[:#]?\s*(?P<value>[A-Z0-9][A-Z0-9/.\-]{3,40})",
    re.IGNORECASE,
)

DATE_CONTEXT = (
    "datum",
    "rechnungsdatum",
    "belegdatum",
    "ausstellungsdatum",
    "briefdatum",
    "fälligkeit",
)
AMOUNT_CONTEXT = (
    "betrag",
    "summe",
    "gesamt",
    "rechnungsbetrag",
    "prämie",
    "praemie",
    "beitrag",
    "abbuchung",
    "zahlung",
)


@dataclass(frozen=True)
class _Hit:
    field: str
    value: str
    normalized_value: str
    confidence: int
    reason: str
    page_no: int | None
    snippet: str
    snippet_html: str


def generate_candidates(document: Document, *, replace_pending: bool = True) -> int:
    """Extrahiert Smart-Inbox-Kandidaten für ein Dokument.

    ``replace_pending`` löscht nur offene heuristische Kandidaten. Bereits
    übernommene oder verworfene Vorschläge bleiben erhalten und verhindern,
    dass derselbe Wert beim erneuten Lauf wieder auftaucht.
    """
    if replace_pending:
        document.extraction_candidates.filter(
            source="heuristic",
            status=ExtractionCandidate.Status.PENDING,
        ).delete()

    existing = {
        (item.field, item.normalized_value or item.value)
        for item in document.extraction_candidates.all()
    }
    hits = _collect_hits(document)
    created = []
    for hit in _best_hits(hits):
        signature = (hit.field, hit.normalized_value or hit.value)
        if signature in existing:
            continue
        existing.add(signature)
        created.append(
            ExtractionCandidate(
                document=document,
                field=hit.field,
                value=hit.value[:512],
                normalized_value=hit.normalized_value[:512],
                confidence=max(0, min(100, hit.confidence)),
                reason=hit.reason[:255],
                source="heuristic",
                source_page=hit.page_no,
                source_snippet=hit.snippet,
                source_snippet_html=hit.snippet_html,
            )
        )

    ExtractionCandidate.objects.bulk_create(created)
    return len(created)


def _collect_hits(document: Document) -> list[_Hit]:
    version = document.current_version
    if version is None:
        return []

    pages = list(version.page_texts.order_by("page_no").values_list("page_no", "text"))
    if not pages and version.ocr_text:
        pages = [(None, version.ocr_text)]

    hits: list[_Hit] = []
    for page_no, text in pages:
        if not text:
            continue
        hits.extend(_date_hits(text, page_no))
        hits.extend(_amount_hits(text, page_no))
        hits.extend(_iban_hits(text, page_no))
        hits.extend(
            _number_hits(
                text,
                page_no,
                CONTRACT_RE,
                ExtractionCandidate.Field.CONTRACT_NUMBER,
            )
        )
        hits.extend(
            _number_hits(
                text,
                page_no,
                POLICY_RE,
                ExtractionCandidate.Field.POLICY_NUMBER,
            )
        )
    return hits


def _best_hits(hits: list[_Hit], *, per_field: int = 3) -> list[_Hit]:
    """Begrenzt die Kandidatenmenge pro Feld auf die stärksten eindeutigen Werte."""
    result: list[_Hit] = []
    grouped: dict[str, list[_Hit]] = {}
    for hit in hits:
        grouped.setdefault(hit.field, []).append(hit)

    for field, items in grouped.items():
        seen = set()
        ranked = sorted(items, key=lambda h: (-h.confidence, h.page_no or 9999))
        for item in ranked:
            key = item.normalized_value or item.value
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
            if len([h for h in result if h.field == field]) >= per_field:
                break
    return result


def _date_hits(text: str, page_no: int | None) -> list[_Hit]:
    hits = []
    today = timezone.localdate()
    future_limit = today + timedelta(days=366)
    for match in DATE_RE.finditer(text):
        normalized = _normalize_date(match)
        if normalized is None:
            continue
        parsed = date.fromisoformat(normalized)
        if parsed.year < 1990 or parsed > future_limit:
            continue
        context = _context(text, match.start(), match.end())
        confidence = 78 if _contains_any(context, DATE_CONTEXT) else 58
        snippet, snippet_html = _snippet(text, match.start(), match.end())
        hits.append(
            _Hit(
                field=ExtractionCandidate.Field.DOCUMENT_DATE,
                value=match.group(0),
                normalized_value=normalized,
                confidence=confidence,
                reason="Datum in Belegkontext erkannt"
                if confidence >= 70
                else "Datum im Dokument erkannt",
                page_no=page_no,
                snippet=snippet,
                snippet_html=snippet_html,
            )
        )
    return hits


def _amount_hits(text: str, page_no: int | None) -> list[_Hit]:
    hits = []
    for match in AMOUNT_RE.finditer(text):
        raw_value = match.group("prefix") or match.group("suffix") or ""
        normalized = _normalize_amount(raw_value)
        if not normalized:
            continue
        context = _context(text, match.start(), match.end())
        confidence = 82 if _contains_any(context, AMOUNT_CONTEXT) else 68
        snippet, snippet_html = _snippet(text, match.start(), match.end())
        hits.append(
            _Hit(
                field=ExtractionCandidate.Field.AMOUNT,
                value=match.group(0).strip(),
                normalized_value=normalized,
                confidence=confidence,
                reason="Betrag mit Währungs- und Rechnungskontext erkannt"
                if confidence >= 80
                else "Betrag mit Währungszeichen erkannt",
                page_no=page_no,
                snippet=snippet,
                snippet_html=snippet_html,
            )
        )
    return hits


def _iban_hits(text: str, page_no: int | None) -> list[_Hit]:
    hits = []
    for match in IBAN_RE.finditer(text):
        normalized = re.sub(r"\s+", "", match.group(0)).upper()
        if not (15 <= len(normalized) <= 34):
            continue
        snippet, snippet_html = _snippet(text, match.start(), match.end())
        hits.append(
            _Hit(
                field=ExtractionCandidate.Field.IBAN,
                value=match.group(0).strip(),
                normalized_value=normalized,
                confidence=95,
                reason="IBAN-Format erkannt",
                page_no=page_no,
                snippet=snippet,
                snippet_html=snippet_html,
            )
        )
    return hits


def _number_hits(
    text: str,
    page_no: int | None,
    pattern: re.Pattern,
    field: str,
) -> list[_Hit]:
    hits = []
    for match in pattern.finditer(text):
        value = match.group("value").strip().rstrip(".,;")
        if len(value) < 4:
            continue
        snippet, snippet_html = _snippet(text, match.start("value"), match.end("value"))
        hits.append(
            _Hit(
                field=field,
                value=value,
                normalized_value=value.upper(),
                confidence=86,
                reason="Nummer direkt nach passendem Feldlabel erkannt",
                page_no=page_no,
                snippet=snippet,
                snippet_html=snippet_html,
            )
        )
    return hits


def _normalize_date(match: re.Match) -> str | None:
    if match.group("iso"):
        try:
            return date.fromisoformat(match.group("iso")).isoformat()
        except ValueError:
            return None
    try:
        parsed = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except (TypeError, ValueError):
        return None
    return parsed.isoformat()


def _normalize_amount(raw: str) -> str:
    value = raw.replace("\u00a0", " ").replace(" ", "")
    value = value.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        return ""
    if amount <= 0:
        return ""
    return f"{amount:.2f}"


def _context(text: str, start: int, end: int, *, radius: int = 90) -> str:
    return text[max(0, start - radius) : min(len(text), end + radius)].lower()


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _snippet(text: str, start: int, end: int, *, radius: int = 100) -> tuple[str, str]:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    raw = text[lo:hi]
    before = text[lo:start]
    value = text[start:end]
    after = text[end:hi]
    snippet = re.sub(r"\s+", " ", raw).strip()
    snippet_html = (
        html.escape(before)
        + "<mark>"
        + html.escape(value)
        + "</mark>"
        + html.escape(after)
    )
    snippet_html = re.sub(r"\s+", " ", snippet_html).strip()
    return snippet, snippet_html
