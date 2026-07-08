"""Erklärbare Aktenvorschläge für die Review-Queue.

Der Service ist absichtlich deterministisch. Er bewertet sichtbare Signale und
persistiert Vorschläge, statt Dokumente automatisch umzuhängen. Dadurch bleibt
die Vorgangsakte fachlich menschlich bestätigt und revisionssicher auditierbar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.db.models import Q
from django.utils.text import slugify

from documents.models import CaseFile, CaseFileCandidate, Document, ExtractionCandidate


MIN_EXISTING_SCORE = 45
MAX_EXISTING_CANDIDATES = 3
TEXT_LIMIT = 14000

IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ \u00a0]?[A-Z0-9]){11,30}\b", re.IGNORECASE)
NUMBER_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"vertrags(?:nummer|nr\.?)|vertrag|kundennummer|mandatsreferenz|"
    r"polizzennummer|polizzen?nr\.?|polizze|versicherungsnummer"
    r")\b\s*[:#]?\s*(?P<value>[A-Z0-9][A-Z0-9/.\-]{3,40})",
    re.IGNORECASE,
)
TERM_RE = re.compile(r"[A-Za-zÄÖÜäöüß0-9][A-Za-zÄÖÜäöüß0-9\-_/]{2,}")
STOPWORDS = {
    "aber",
    "alle",
    "auch",
    "auf",
    "aus",
    "bei",
    "bitte",
    "dass",
    "der",
    "die",
    "das",
    "dem",
    "den",
    "des",
    "ein",
    "eine",
    "einer",
    "eines",
    "für",
    "mit",
    "nicht",
    "oder",
    "seite",
    "und",
    "vom",
    "von",
    "zur",
}


@dataclass
class _Profile:
    title: str
    text: str
    correspondent_id: int | None
    correspondent_name: str
    document_type_id: int | None
    document_type_name: str
    tag_ids: set[int] = field(default_factory=set)
    tag_names: set[str] = field(default_factory=set)
    identifiers: dict[str, set[str]] = field(default_factory=dict)
    terms: set[str] = field(default_factory=set)


def generate_candidates(document: Document, *, replace_pending: bool = True) -> int:
    """Erzeugt Aktenvorschläge für ein Dokument und liefert die Anzahl neuer Zeilen.

    Bereits übernommene oder verworfene Signaturen werden respektiert. Dadurch
    taucht ein bewusst verworfener Vorschlag nicht beim nächsten Lauf wieder auf.
    """
    if document.case_file_id:
        if replace_pending:
            document.case_file_candidates.filter(
                source="heuristic",
                status=CaseFileCandidate.Status.PENDING,
            ).delete()
        return 0

    if replace_pending:
        document.case_file_candidates.filter(
            source="heuristic",
            status=CaseFileCandidate.Status.PENDING,
        ).delete()

    existing_signatures = set(
        document.case_file_candidates.values_list("signature", flat=True)
    )
    profile = _document_profile(document)
    candidates = _existing_case_candidates(document, profile)
    candidates.extend(_new_case_candidates(document, profile, candidates))

    rows = []
    for data in candidates:
        if data["signature"] in existing_signatures:
            continue
        existing_signatures.add(data["signature"])
        rows.append(
            CaseFileCandidate(
                document=document,
                case_file=data.get("case_file"),
                kind=data["kind"],
                suggested_title=data.get("suggested_title", ""),
                signature=data["signature"],
                score=max(0, min(100, data["score"])),
                reason=data["reason"][:255],
                signals=data["signals"],
                source="heuristic",
            )
        )

    CaseFileCandidate.objects.bulk_create(rows)
    return len(rows)


def _existing_case_candidates(document: Document, profile: _Profile) -> list[dict]:
    qs = (
        CaseFile.objects.filter(owner=document.owner)
        .filter(status__in=[CaseFile.Status.ACTIVE, CaseFile.Status.WAITING])
        .exclude(documents=document)
        .prefetch_related(
            "documents__tags",
            "documents__custom_field_values__field",
            "documents__extraction_candidates",
            "documents__current_version__page_texts",
        )
    )

    ranked = []
    for case_file in qs:
        score, signals = _score_case(profile, _case_profile(case_file))
        if score < MIN_EXISTING_SCORE:
            continue
        ranked.append(
            {
                "kind": CaseFileCandidate.Kind.EXISTING_CASE,
                "case_file": case_file,
                "signature": f"existing:{case_file.pk}",
                "score": score,
                "reason": _reason(signals),
                "signals": signals,
            }
        )

    ranked.sort(key=lambda item: (-item["score"], item["case_file"].title.lower()))
    return ranked[:MAX_EXISTING_CANDIDATES]


def _new_case_candidates(
    document: Document,
    profile: _Profile,
    existing: list[dict],
) -> list[dict]:
    best_existing = max((item["score"] for item in existing), default=0)
    if best_existing >= 70:
        return []

    title = _suggested_title(document, profile)
    signals = [
        {
            "type": "new_case",
            "label": "Neue Akte vorgeschlagen",
            "value": title,
            "weight": 52,
        }
    ]
    for kind in ("contract", "policy", "iban"):
        value = next(iter(profile.identifiers.get(kind, [])), "")
        if value:
            signals.append(
                {
                    "type": kind,
                    "label": _identifier_label(kind),
                    "value": value,
                    "weight": 10,
                }
            )
            break

    score = 58 if len(signals) == 1 else 68
    signature = f"new:{slugify(title)[:90] or document.pk}"
    return [
        {
            "kind": CaseFileCandidate.Kind.NEW_CASE,
            "suggested_title": title,
            "signature": signature,
            "score": score,
            "reason": "Keine starke bestehende Akte gefunden",
            "signals": signals,
        }
    ]


def _score_case(document: _Profile, case: _Profile) -> tuple[int, list[dict]]:
    score = 0
    signals: list[dict] = []

    if document.correspondent_id and document.correspondent_id == case.correspondent_id:
        score += _signal(signals, "correspondent", "Gleicher Korrespondent", document.correspondent_name, 22)

    if document.document_type_id and document.document_type_id == case.document_type_id:
        score += _signal(signals, "document_type", "Gleicher Dokumenttyp", document.document_type_name, 12)

    shared_tags = document.tag_ids & case.tag_ids
    if shared_tags:
        names = sorted(document.tag_names & case.tag_names)[:4]
        weight = min(18, 6 * len(shared_tags))
        score += _signal(signals, "tags", "Gemeinsame Tags", ", ".join(names), weight)

    for kind, weight in (("contract", 34), ("policy", 34), ("iban", 40)):
        shared = document.identifiers.get(kind, set()) & case.identifiers.get(kind, set())
        if shared:
            score += _signal(
                signals,
                kind,
                _identifier_label(kind),
                sorted(shared)[0],
                weight,
            )

    shared_terms = sorted(document.terms & case.terms)
    if len(shared_terms) >= 3:
        weight = min(24, len(shared_terms) * 4)
        score += _signal(
            signals,
            "terms",
            "Gemeinsame Schlüsselbegriffe",
            ", ".join(shared_terms[:6]),
            weight,
        )

    return min(100, score), signals


def _signal(signals: list[dict], type_: str, label: str, value: str, weight: int) -> int:
    signals.append({"type": type_, "label": label, "value": value, "weight": weight})
    return weight


def _document_profile(document: Document) -> _Profile:
    text = _document_text(document)
    identifiers = _identifiers(document, text)
    terms = _terms(
        " ".join(
            [
                document.title,
                document.correspondent.name if document.correspondent_id else "",
                document.document_type.name if document.document_type_id else "",
                text,
            ]
        )
    )
    tag_ids = set(document.tags.values_list("id", flat=True))
    tag_names = set(document.tags.values_list("name", flat=True))
    return _Profile(
        title=document.title,
        text=text,
        correspondent_id=document.correspondent_id,
        correspondent_name=document.correspondent.name if document.correspondent_id else "",
        document_type_id=document.document_type_id,
        document_type_name=document.document_type.name if document.document_type_id else "",
        tag_ids=tag_ids,
        tag_names=tag_names,
        identifiers=identifiers,
        terms=terms,
    )


def _case_profile(case_file: CaseFile) -> _Profile:
    docs = list(case_file.documents.all()[:8])
    text_parts = [case_file.title, case_file.description, case_file.ai_summary]
    identifiers: dict[str, set[str]] = {"contract": set(), "policy": set(), "iban": set()}
    tag_ids: set[int] = set()
    tag_names: set[str] = set()
    correspondent_ids = []
    document_type_ids = []

    for doc in docs:
        text = _document_text(doc)
        text_parts.extend([doc.title, text[:4000]])
        for key, values in _identifiers(doc, text).items():
            identifiers.setdefault(key, set()).update(values)
        tag_ids.update(doc.tags.values_list("id", flat=True))
        tag_names.update(doc.tags.values_list("name", flat=True))
        if doc.correspondent_id:
            correspondent_ids.append(doc.correspondent_id)
        if doc.document_type_id:
            document_type_ids.append(doc.document_type_id)

    text = "\n".join(text_parts)[:TEXT_LIMIT]
    return _Profile(
        title=case_file.title,
        text=text,
        correspondent_id=_dominant(correspondent_ids),
        correspondent_name="",
        document_type_id=_dominant(document_type_ids),
        document_type_name="",
        tag_ids=tag_ids,
        tag_names=tag_names,
        identifiers=identifiers,
        terms=_terms(text),
    )


def _document_text(document: Document) -> str:
    version = document.current_version
    if version is None:
        return ""
    pages = list(version.page_texts.order_by("page_no").values_list("text", flat=True))
    text = "\n".join(page for page in pages if page) or version.ocr_text or ""
    return text[:TEXT_LIMIT]


def _identifiers(document: Document, text: str) -> dict[str, set[str]]:
    identifiers: dict[str, set[str]] = {"contract": set(), "policy": set(), "iban": set()}
    combined = "\n".join([document.title, text])

    for match in IBAN_RE.finditer(combined):
        identifiers["iban"].add(re.sub(r"\s+", "", match.group(0)).upper())
    for match in NUMBER_CONTEXT_RE.finditer(combined):
        value = _normalize_identifier(match.group("value"))
        if value:
            context = match.group(0).lower()
            key = "policy" if "polizz" in context or "versicherung" in context else "contract"
            identifiers[key].add(value)

    for candidate in document.extraction_candidates.filter(
        field__in=[
            ExtractionCandidate.Field.IBAN,
            ExtractionCandidate.Field.CONTRACT_NUMBER,
            ExtractionCandidate.Field.POLICY_NUMBER,
        ]
    ):
        value = _normalize_identifier(candidate.normalized_value or candidate.value)
        if not value:
            continue
        if candidate.field == ExtractionCandidate.Field.IBAN:
            identifiers["iban"].add(value)
        elif candidate.field == ExtractionCandidate.Field.POLICY_NUMBER:
            identifiers["policy"].add(value)
        else:
            identifiers["contract"].add(value)

    custom_values = document.custom_field_values.select_related("field").filter(
        Q(field__name__icontains="iban")
        | Q(field__name__icontains="vertrag")
        | Q(field__name__icontains="poliz")
        | Q(field__name__icontains="versicher")
    )
    for value in custom_values:
        normalized = _normalize_identifier(value.value)
        if not normalized:
            continue
        name = value.field.name.lower()
        if "iban" in name:
            identifiers["iban"].add(normalized)
        elif "poliz" in name or "versicher" in name:
            identifiers["policy"].add(normalized)
        else:
            identifiers["contract"].add(normalized)

    return identifiers


def _terms(text: str) -> set[str]:
    result = set()
    for raw in TERM_RE.findall(text.lower()):
        term = raw.strip("-_/")
        if len(term) < 4 or term in STOPWORDS or term.isdigit():
            continue
        result.add(term)
        if len(result) >= 120:
            break
    return result


def _dominant(values: list[int]) -> int | None:
    if not values:
        return None
    return max(set(values), key=values.count)


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[\s\u00a0]+", "", value or "").upper().strip(".,;:")


def _identifier_label(kind: str) -> str:
    return {
        "contract": "Gleiche Vertragsnummer",
        "policy": "Gleiche Polizzen-/Versicherungsnummer",
        "iban": "Gleiche IBAN",
    }.get(kind, kind)


def _reason(signals: list[dict]) -> str:
    if not signals:
        return "Ähnliche Akte erkannt"
    return ", ".join(signal["label"] for signal in signals[:3])


def _suggested_title(document: Document, profile: _Profile) -> str:
    parts = []
    if profile.correspondent_name:
        parts.append(profile.correspondent_name)
    if profile.document_type_name:
        parts.append(profile.document_type_name)

    identifier = (
        next(iter(profile.identifiers.get("policy", [])), "")
        or next(iter(profile.identifiers.get("contract", [])), "")
    )
    if identifier:
        parts.append(identifier)

    title = " · ".join(parts) or document.title or "Neue Akte"
    return title[:255]
