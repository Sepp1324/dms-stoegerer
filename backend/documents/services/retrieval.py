"""Hybrid-Retrieval fuer den Dokumenten-Copilot.

Der Copilot darf keine Antwort ohne belastbare Quellen geben. Dieser Service
buendelt deshalb die lokalen Signale des DMS - Seitentexte, OCR, Metadaten,
Entitaeten, Vertraege und Akten - zu zitierbaren Source-Cards. Die KI-Schicht
bekommt danach nur noch diese kurzen Quellen, nicht den gesamten Dokumentbestand.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from typing import Iterable

from documents.models import ContractRecord, Document, DocumentEntity

_TOKEN_RE = re.compile(r"[\wÄÖÜäöüß-]{2,}")

_STOPWORDS = {
    "aber",
    "alle",
    "alles",
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
    "er",
    "es",
    "für",
    "hat",
    "ich",
    "im",
    "in",
    "ist",
    "mit",
    "nach",
    "oder",
    "sich",
    "und",
    "vom",
    "von",
    "wann",
    "war",
    "was",
    "welche",
    "welchem",
    "welchen",
    "wer",
    "wie",
    "wir",
    "wo",
    "zu",
    "zum",
    "zur",
}

# Kleine DMS-Domaenenexpansion: genug, um familiaere Fragen robust zu machen,
# ohne einen schwergewichtigen semantischen Index einzufuehren.
_EXPANSIONS = {
    "akte": {"vorgang", "case", "fall"},
    "akten": {"vorgang", "case", "fall"},
    "abo": {"vertrag", "subscription"},
    "abos": {"vertrag", "subscription"},
    "faellig": {"faelligkeit", "falligkeit", "next_due_on", "zahlung"},
    "fallig": {"faelligkeit", "falligkeit", "next_due_on", "zahlung"},
    "kuendigung": {"kundigung", "cancel_until", "notice", "vertrag"},
    "kundigung": {"kuendigung", "cancel_until", "notice", "vertrag"},
    "laeuft": {"ende", "ends_on", "cancel_until", "faelligkeit", "vertrag"},
    "lauft": {"ende", "ends_on", "cancel_until", "faelligkeit", "vertrag"},
    "laufen": {"ende", "ends_on", "cancel_until", "faelligkeit", "vertrag"},
    "polizze": {"versicherung", "vertrag", "policy_number"},
    "praemie": {"pramie", "betrag", "amount", "zahlung"},
    "pramie": {"praemie", "betrag", "amount", "zahlung"},
    "rechnung": {"betrag", "iban", "zahlung"},
    "rechnungen": {"rechnung", "betrag", "iban", "zahlung"},
    "vertrag": {"vertragsnummer", "polizze", "versicherung", "contract_number"},
    "vertrage": {"vertrag", "vertragsnummer", "polizze", "versicherung"},
    "versicherung": {"polizze", "vertrag"},
    "versicherungen": {"versicherung", "polizze", "vertrag"},
}


@dataclass(frozen=True)
class RetrievalFilters:
    """Optionale, bereits user-gepruefte Filter fuer den Copilot."""

    folder: int | str | None = None
    case_file: int | None = None
    entity: int | None = None
    contract: int | None = None
    created_from: str | None = None
    created_to: str | None = None

    def as_dict(self) -> dict:
        return {
            "folder": self.folder,
            "case_file": self.case_file,
            "entity": self.entity,
            "contract": self.contract,
            "created_from": self.created_from,
            "created_to": self.created_to,
        }


@dataclass
class _Candidate:
    score: float
    document: Document
    text: str
    page: int | None
    source_type: str
    reason: str
    matched_terms: list[str] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    contract: dict | None = None
    case_file: dict | None = None


def query_terms(question: str) -> list[str]:
    """Extrahiert robuste Suchterme aus einer deutschen Nutzerfrage."""
    terms: list[str] = []
    for raw in _TOKEN_RE.findall(question or ""):
        folded = _fold(raw)
        if len(folded) < 3 or folded in _STOPWORDS:
            continue
        if folded not in terms:
            terms.append(folded)
    return terms[:14]


def expanded_terms(terms: Iterable[str]) -> list[str]:
    """Erweitert Query-Terme um kleine DMS-Synonyme und Normalformen."""
    out: list[str] = []
    for term in terms:
        for value in [term, *_EXPANSIONS.get(term, set())]:
            folded = _fold(value)
            if folded and folded not in out:
                out.append(folded)
    return out[:36]


def retrieve_context(
    question: str,
    documents: Iterable[Document],
    *,
    limit: int = 8,
    filters: RetrievalFilters | None = None,
) -> dict:
    """Findet die besten zitierbaren Quellen fuer eine Copilot-Frage.

    ``documents`` muss bereits rechtlich gescoped sein (Owner/Admin/Filter). Der
    Service nutzt danach nur diesen Bestand und kann damit gefahrlos aus Views,
    Management Commands oder spaeter Celery/Embeddings aufgerufen werden.
    """
    docs = list(documents)
    terms = query_terms(question)
    terms_with_expansion = expanded_terms(terms)
    doc_ids = [doc.id for doc in docs]
    entity_links = _entity_links_by_document(doc_ids)
    contracts = _contracts_by_document(doc_ids)

    candidates: list[_Candidate] = []
    for document in docs:
        entities = entity_links.get(document.id, [])
        contract = contracts.get(document.id)
        case_file = _case_file_payload(document)
        metadata_text = _metadata_text(document, entities, contract, case_file)
        metadata_score = _score(metadata_text, terms_with_expansion, boost=1.15)
        metadata_matches = _matched_terms(metadata_text, terms_with_expansion)

        version = document.current_version
        page_texts = list(version.page_texts.all()) if version else []
        emitted_text_candidate = False
        for page in page_texts:
            page_text = page.text or ""
            page_score = _score(page_text, terms_with_expansion, boost=1.0)
            if page_score <= 0:
                continue
            emitted_text_candidate = True
            candidates.append(
                _Candidate(
                    score=page_score + min(metadata_score, 10),
                    document=document,
                    page=page.page_no,
                    text=page_text,
                    source_type="page_text",
                    reason=_reason(metadata_matches, "Seitentext"),
                    matched_terms=_matched_terms(
                        f"{page_text} {metadata_text}", terms_with_expansion
                    ),
                    entities=entities,
                    contract=_contract_payload(contract),
                    case_file=case_file,
                )
            )

        if version and not emitted_text_candidate and (version.ocr_text or "").strip():
            ocr_score = _score(version.ocr_text, terms_with_expansion, boost=0.85)
            if ocr_score > 0:
                emitted_text_candidate = True
                candidates.append(
                    _Candidate(
                        score=ocr_score + min(metadata_score, 8),
                        document=document,
                        page=None,
                        text=version.ocr_text,
                        source_type="ocr_text",
                        reason=_reason(metadata_matches, "OCR-Text"),
                        matched_terms=_matched_terms(
                            f"{version.ocr_text} {metadata_text}", terms_with_expansion
                        ),
                        entities=entities,
                        contract=_contract_payload(contract),
                        case_file=case_file,
                    )
                )

        # Wenn die Frage ueber Entitaet/Vertrag/Akte matched, aber der Seitentext
        # selbst den Begriff nicht enthaelt, bleibt die Quelle trotzdem belegbar:
        # die Source-Card erklaert dann explizit den Metadaten-/Graph-Treffer.
        if metadata_score > 0 and not emitted_text_candidate:
            candidates.append(
                _Candidate(
                    score=metadata_score,
                    document=document,
                    page=None,
                    text=metadata_text,
                    source_type="metadata",
                    reason=_reason(metadata_matches, "Metadaten/Gedächtnis"),
                    matched_terms=metadata_matches,
                    entities=entities,
                    contract=_contract_payload(contract),
                    case_file=case_file,
                )
            )

    candidates.sort(key=lambda item: (item.score, item.document.added_at, item.document.id), reverse=True)
    sources = _dedupe_sources(candidates, terms_with_expansion, limit=limit)
    return {
        "query_terms": terms,
        "expanded_terms": terms_with_expansion,
        "total_candidates": len(candidates),
        "filters": (filters or RetrievalFilters()).as_dict(),
        "sources": sources,
    }


def format_sources_for_prompt(sources: list[dict]) -> str:
    """Formatiert Source-Cards kompakt fuer den KI-Prompt."""
    blocks = []
    for source in sources:
        context_bits = []
        if source.get("case_file"):
            context_bits.append(f"Akte: {source['case_file']['title']}")
        if source.get("contract"):
            contract = source["contract"]
            provider = contract.get("provider") or "-"
            number = contract.get("contract_number") or "-"
            context_bits.append(f"Vertrag: {provider} / {number}")
        if source.get("entities"):
            names = ", ".join(entity["name"] for entity in source["entities"][:6])
            context_bits.append(f"Entitaeten: {names}")
        blocks.append(
            "\n".join(
                [
                    f"[{source['id']}] Dokument: {source['document_title']}",
                    f"Ordner: {source.get('folder_path') or '-'}",
                    f"Seite: {source.get('page') or '-'}",
                    f"Grund: {source.get('reason') or '-'}",
                    *(context_bits or []),
                    f"Ausschnitt: {source.get('snippet') or '-'}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _entity_links_by_document(doc_ids: list[int]) -> dict[int, list[dict]]:
    if not doc_ids:
        return {}
    links = (
        DocumentEntity.objects.select_related("entity")
        .prefetch_related("entity__identifiers")
        .filter(document_id__in=doc_ids)
        .order_by("-confidence", "role", "entity__name")
    )
    out: dict[int, list[dict]] = {}
    seen: set[tuple[int, int, str]] = set()
    for link in links:
        key = (link.document_id, link.entity_id, link.role)
        if key in seen:
            continue
        seen.add(key)
        identifiers = [
            {
                "kind": identifier.kind,
                "value": identifier.value,
            }
            for identifier in link.entity.identifiers.all()[:6]
        ]
        out.setdefault(link.document_id, []).append(
            {
                "id": link.entity_id,
                "kind": link.entity.kind,
                "name": link.entity.name,
                "role": link.role,
                "confidence": link.confidence,
                "identifiers": identifiers,
            }
        )
    return out


def _contracts_by_document(doc_ids: list[int]) -> dict[int, ContractRecord]:
    if not doc_ids:
        return {}
    return {
        record.document_id: record
        for record in ContractRecord.objects.select_related("case_file").filter(
            document_id__in=doc_ids
        )
    }


def _metadata_text(
    document: Document,
    entities: list[dict],
    contract: ContractRecord | None,
    case_file: dict | None,
) -> str:
    tags = " ".join(tag.name for tag in document.tags.all())
    parts = [
        document.title,
        document.correspondent.name if document.correspondent_id else "",
        document.document_type.name if document.document_type_id else "",
        document.folder.full_path if document.folder_id else "",
        document.mail_subject,
        document.mail_sender,
        tags,
        f"ASN {document.asn}" if document.asn else "",
    ]
    if case_file:
        parts.extend(["Akte Vorgang", case_file["title"], case_file.get("status_label", "")])
    for entity in entities:
        parts.extend(
            [
                "Entitaet",
                entity["kind"],
                entity["name"],
                entity["role"],
                " ".join(identifier["value"] for identifier in entity.get("identifiers", [])),
            ]
        )
    if contract:
        parts.extend(_contract_text_parts(contract))
    return " ".join(str(part or "") for part in parts)


def _contract_text_parts(record: ContractRecord) -> list[str]:
    date_parts = [
        f"Beginn {record.starts_on}" if record.starts_on else "",
        f"Ende {record.ends_on}" if record.ends_on else "",
        f"Kuendigung Kündigung cancel_until {record.cancel_until}" if record.cancel_until else "",
        f"Faelligkeit Fälligkeit next_due_on {record.next_due_on}" if record.next_due_on else "",
    ]
    return [
        "Vertrag Vertragsnummer Polizze Versicherung Abo",
        record.get_contract_type_display(),
        record.provider,
        record.contract_number,
        record.get_status_display(),
        record.get_billing_cycle_display(),
        str(record.amount or ""),
        record.currency,
        *date_parts,
        record.notes,
    ]


def _contract_payload(record: ContractRecord | None) -> dict | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "provider": record.provider,
        "contract_number": record.contract_number,
        "contract_type": record.contract_type,
        "contract_type_label": record.get_contract_type_display(),
        "status": record.status,
        "status_label": record.get_status_display(),
        "amount": str(record.amount) if record.amount is not None else None,
        "currency": record.currency,
        "cancel_until": record.cancel_until.isoformat() if record.cancel_until else None,
        "next_due_on": record.next_due_on.isoformat() if record.next_due_on else None,
        "ends_on": record.ends_on.isoformat() if record.ends_on else None,
    }


def _case_file_payload(document: Document) -> dict | None:
    if not document.case_file_id:
        return None
    return {
        "id": document.case_file_id,
        "title": document.case_file.title,
        "status": document.case_file.status,
        "status_label": document.case_file.get_status_display(),
    }


def _score(text: str, terms: list[str], *, boost: float) -> float:
    folded = _fold(text)
    if not folded.strip():
        return 0
    if not terms:
        return 1
    score = 0.0
    for term in terms:
        count = folded.count(term)
        if count:
            score += boost * (5 + min(count, 8))
    return score


def _matched_terms(text: str, terms: list[str]) -> list[str]:
    folded = _fold(text)
    return [term for term in terms if term in folded][:8]


def _reason(metadata_matches: list[str], fallback: str) -> str:
    if metadata_matches:
        readable = ", ".join(metadata_matches[:4])
        return f"{fallback} + Kontexttreffer ({readable})"
    return fallback


def _dedupe_sources(candidates: list[_Candidate], terms: list[str], *, limit: int) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[int, int | None, str]] = set()
    for candidate in candidates:
        key = (candidate.document.id, candidate.page, candidate.source_type)
        if key in seen:
            continue
        seen.add(key)
        snippet = _snippet(candidate.text, terms)
        out.append(
            {
                "id": f"S{len(out) + 1}",
                "document": candidate.document.id,
                "document_title": candidate.document.title,
                "folder_path": candidate.document.folder.full_path
                if candidate.document.folder_id
                else None,
                "page": candidate.page,
                "snippet": snippet,
                "snippet_html": _highlight(snippet, terms),
                "score": round(candidate.score, 2),
                "reason": candidate.reason,
                "source_type": candidate.source_type,
                "matched_terms": candidate.matched_terms,
                "entities": candidate.entities[:8],
                "contract": candidate.contract,
                "case_file": candidate.case_file,
            }
        )
        if len(out) >= limit:
            break
    return out


def _snippet(text: str, terms: list[str], *, radius: int = 460) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return ""
    folded = _fold(cleaned)
    positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
    pos = min(positions) if positions else 0
    start = max(0, pos - radius // 2)
    end = min(len(cleaned), start + radius)
    start = max(0, end - radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(cleaned) else ""
    return f"{prefix}{cleaned[start:end]}{suffix}"


def _highlight(text: str, terms: list[str]) -> str:
    safe = escape(text)
    # Original und gefaltete Begriffe sind nicht immer identisch (ä/ae/ß). Wir
    # markieren nur direkte sichere Treffer und lassen semantische Treffer ueber
    # reason/matched_terms sichtbar werden.
    for term in sorted(set(terms), key=len, reverse=True):
        if not term:
            continue
        safe = re.sub(
            re.escape(escape(term)),
            lambda match: f"<mark>{match.group(0)}</mark>",
            safe,
            flags=re.IGNORECASE,
        )
    return safe


def _fold(value: str) -> str:
    folded = (value or "").lower()
    folded = (
        folded.replace("ä", "a")
        .replace("ö", "o")
        .replace("ü", "u")
        .replace("ß", "ss")
    )
    return re.sub(r"\s+", " ", folded)
