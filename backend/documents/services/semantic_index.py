"""Providerfreier semantischer Index fuer Dokumente.

V1 nutzt lokale Hash-Embeddings: deterministisch, schnell, keine API-Kosten und
keine pgvector-Abhaengigkeit. Die fachliche API ist trotzdem so geschnitten,
dass spaeter echte Embedding-Provider oder pgvector darunter passen.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from html import escape
from typing import Iterable

from django.db.models import F
from django.utils import timezone

from documents.models import Document, DocumentEmbedding, DocumentVersion

EMBEDDING_MODEL = "local-hash-v1"
DIMENSION = 192
MAX_CHUNKS_PER_VERSION = 80
MAX_WORDS_PER_CHUNK = 220
OVERLAP_WORDS = 35
MIN_SIMILARITY = 0.16

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
    "gibt",
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

_SYNONYMS = {
    "abo": {"vertrag", "subscription"},
    "abos": {"vertrag", "subscription"},
    "bank": {"konto", "iban"},
    "faelligkeit": {"zahlung", "termin", "due"},
    "falligkeit": {"zahlung", "termin", "due"},
    "kuendigung": {"vertrag", "frist", "cancel"},
    "kundigung": {"vertrag", "frist", "cancel"},
    "polizze": {"versicherung", "vertrag"},
    "praemie": {"betrag", "zahlung", "versicherung"},
    "pramie": {"betrag", "zahlung", "versicherung"},
    "rechnung": {"betrag", "zahlung", "iban"},
    "vertrag": {"polizze", "versicherung", "abo"},
    "versicherung": {"polizze", "vertrag"},
}


@dataclass(frozen=True)
class ChunkSpec:
    page_no: int | None
    source: str
    text: str


def sync_document_embeddings(document: Document, *, version: DocumentVersion | None = None) -> dict:
    """Erzeugt den semantischen Index fuer die aktuelle oder angegebene Version."""
    version = version or document.current_version
    if version is None:
        return {"status": "missing_version", "created": 0, "deleted": 0}

    chunks = build_chunks(document, version)
    deleted, _ = DocumentEmbedding.objects.filter(
        version=version, embedding_model=EMBEDDING_MODEL
    ).delete()
    rows = []
    for index, chunk in enumerate(chunks[:MAX_CHUNKS_PER_VERSION]):
        vector, magnitude, token_count = embed_text(chunk.text)
        if magnitude <= 0:
            continue
        rows.append(
            DocumentEmbedding(
                document=document,
                version=version,
                page_no=chunk.page_no,
                chunk_index=index,
                source=chunk.source,
                text=chunk.text,
                text_hash=hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
                embedding_model=EMBEDDING_MODEL,
                dimension=DIMENSION,
                vector=vector,
                magnitude=magnitude,
                token_count=token_count,
            )
        )
    if rows:
        DocumentEmbedding.objects.bulk_create(rows)
    return {
        "status": "indexed" if rows else "empty",
        "created": len(rows),
        "deleted": deleted,
        "version": version.id,
        "model": EMBEDDING_MODEL,
    }


def build_chunks(document: Document, version: DocumentVersion) -> list[ChunkSpec]:
    """Baut Text-Chunks aus Seitentexten, OCR-Fallback und Metadaten."""
    chunks: list[ChunkSpec] = []
    page_texts = list(version.page_texts.order_by("page_no"))
    if page_texts:
        for page in page_texts:
            for part in split_text(page.text):
                chunks.append(
                    ChunkSpec(
                        page_no=page.page_no,
                        source=DocumentEmbedding.Source.PAGE_TEXT,
                        text=part,
                    )
                )
    elif (version.ocr_text or "").strip():
        for part in split_text(version.ocr_text):
            chunks.append(
                ChunkSpec(
                    page_no=None,
                    source=DocumentEmbedding.Source.OCR_TEXT,
                    text=part,
                )
            )

    metadata = metadata_text(document)
    if metadata.strip():
        chunks.append(
            ChunkSpec(
                page_no=None,
                source=DocumentEmbedding.Source.METADATA,
                text=metadata,
            )
        )
    return chunks


def split_text(text: str) -> list[str]:
    """Teilt Text in ueberlappende Wort-Chunks."""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    words = cleaned.split()
    if not words:
        return []
    if len(words) <= MAX_WORDS_PER_CHUNK:
        return [cleaned]

    chunks = []
    step = MAX_WORDS_PER_CHUNK - OVERLAP_WORDS
    for start in range(0, len(words), step):
        window = words[start : start + MAX_WORDS_PER_CHUNK]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + MAX_WORDS_PER_CHUNK >= len(words):
            break
    return chunks


def metadata_text(document: Document) -> str:
    """Verdichtet Dokumentkontext zu einem eigenen semantischen Chunk."""
    parts = [
        document.title,
        document.correspondent.name if document.correspondent_id else "",
        document.document_type.name if document.document_type_id else "",
        document.folder.full_path if document.folder_id else "",
        document.case_file.title if document.case_file_id else "",
        document.mail_subject,
        document.mail_sender,
        " ".join(tag.name for tag in document.tags.all()),
        f"ASN {document.asn}" if document.asn else "",
    ]
    if hasattr(document, "contract_record"):
        contract = document.contract_record
        parts.extend(
            [
                "Vertrag",
                contract.provider,
                contract.contract_number,
                contract.get_contract_type_display(),
                contract.get_status_display(),
                str(contract.amount or ""),
                str(contract.cancel_until or ""),
                str(contract.next_due_on or ""),
                str(contract.ends_on or ""),
            ]
        )
    return " ".join(str(part or "") for part in parts)


def embed_text(text: str) -> tuple[list[float], float, int]:
    """Erzeugt ein lokales Hash-Embedding aus Tokens, Synonymen und Bigrams."""
    tokens = tokenize(text)
    if not tokens:
        return [0.0] * DIMENSION, 0.0, 0

    features: list[str] = []
    for token in tokens:
        features.append(token)
        features.extend(sorted(_SYNONYMS.get(token, set())))
    features.extend(f"{a}_{b}" for a, b in zip(tokens, tokens[1:]))

    counts = Counter(features)
    vector = [0.0] * DIMENSION
    for feature, count in counts.items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        raw = int.from_bytes(digest, "big")
        index = raw % DIMENSION
        sign = -1.0 if (raw >> 8) & 1 else 1.0
        # log-Skalierung verhindert, dass sehr haeufige Woerter den Vektor kapern.
        vector[index] += sign * (1.0 + math.log(count))
    magnitude = math.sqrt(sum(value * value for value in vector))
    return vector, magnitude, len(tokens)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text or ""):
        token = fold(raw)
        if len(token) < 3 or token in _STOPWORDS:
            continue
        tokens.append(token)
        # Primitive deutsche Dekomposition fuer die DMS-Domaene:
        # "versicherungspolizze" soll die vorhandenen Chunks mit
        # "Versicherung" + "Polizze" treffen, ohne ein NLP-Paket einzubauen.
        for known in _SYNONYMS:
            if known != token and len(known) >= 5 and known in token:
                tokens.append(known)
    return tokens


def search_documents(
    question: str,
    documents: Iterable[Document],
    *,
    limit: int = 8,
    min_score: float = MIN_SIMILARITY,
) -> list[dict]:
    """Sucht semantisch in einem bereits owner-gescopten Dokumentbestand."""
    docs = list(documents)
    doc_ids = [doc.id for doc in docs]
    if not doc_ids:
        return []

    query_vector, query_magnitude, _token_count = embed_text(question)
    if query_magnitude <= 0:
        return []

    chunks = (
        DocumentEmbedding.objects.select_related("document", "document__folder")
        .filter(
            document_id__in=doc_ids,
            embedding_model=EMBEDDING_MODEL,
            version_id=F("document__current_version_id"),
        )
        .order_by("-generated_at")
    )
    candidates = []
    for chunk in chunks:
        score = cosine(query_vector, query_magnitude, chunk.vector, chunk.magnitude)
        if score < min_score:
            continue
        candidates.append((score, chunk))
    candidates.sort(key=lambda item: (item[0], item[1].document.added_at), reverse=True)

    out = []
    seen: set[tuple[int, int | None]] = set()
    terms = tokenize(question)
    for score, chunk in candidates:
        key = (chunk.document_id, chunk.page_no)
        if key in seen:
            continue
        seen.add(key)
        snippet = snippet_around(chunk.text, terms)
        out.append(
            {
                "document": chunk.document_id,
                "document_title": chunk.document.title,
                "folder_path": chunk.document.folder.full_path
                if chunk.document.folder_id
                else None,
                "page": chunk.page_no,
                "snippet": snippet,
                "snippet_html": highlight(snippet, terms),
                "score": round(score, 4),
                "reason": f"Semantischer Treffer ({chunk.get_source_display()})",
                "source_type": "semantic",
                "matched_terms": terms[:8],
                "semantic_model": chunk.embedding_model,
                "entities": [],
                "contract": None,
                "case_file": None,
            }
        )
        if len(out) >= limit:
            break
    return out


def similar_documents(document: Document, visible_documents: Iterable[Document], *, limit: int = 6) -> list[dict]:
    """Findet aehnliche sichtbare Dokumente anhand der aktuellen Version."""
    base_chunks = list(
        DocumentEmbedding.objects.filter(
            document=document,
            version=document.current_version,
            embedding_model=EMBEDDING_MODEL,
        )
    )
    if not base_chunks:
        return []

    base_vector, base_magnitude = centroid(base_chunks)
    if base_magnitude <= 0:
        return []

    visible_ids = [doc.id for doc in visible_documents if doc.id != document.id]
    if not visible_ids:
        return []

    best_by_doc: dict[int, tuple[float, DocumentEmbedding]] = {}
    chunks = (
        DocumentEmbedding.objects.select_related("document", "document__folder")
        .filter(
            document_id__in=visible_ids,
            embedding_model=EMBEDDING_MODEL,
            version_id=F("document__current_version_id"),
        )
        .order_by("-generated_at")
    )
    for chunk in chunks:
        score = cosine(base_vector, base_magnitude, chunk.vector, chunk.magnitude)
        if score < MIN_SIMILARITY:
            continue
        current = best_by_doc.get(chunk.document_id)
        if current is None or score > current[0]:
            best_by_doc[chunk.document_id] = (score, chunk)

    ranked = sorted(
        best_by_doc.values(),
        key=lambda item: (item[0], item[1].document.added_at),
        reverse=True,
    )
    results = []
    for score, chunk in ranked[:limit]:
        results.append(
            {
                "document": chunk.document_id,
                "document_title": chunk.document.title,
                "folder_path": chunk.document.folder.full_path
                if chunk.document.folder_id
                else None,
                "page": chunk.page_no,
                "score": round(score, 4),
                "reason": f"Ähnlicher {chunk.get_source_display()}",
                "snippet": snippet_around(chunk.text, []),
                "snippet_html": escape(snippet_around(chunk.text, [])),
            }
        )
    return results


def embedding_health(visible_documents: Iterable[Document] | None = None) -> dict:
    """Kompakter Betriebsstatus des semantischen Index."""
    docs = visible_documents if visible_documents is not None else Document.objects.all()
    docs = docs.exclude(current_version__isnull=True)
    doc_count = docs.count()
    indexed_docs = (
        DocumentEmbedding.objects.filter(
            document__in=docs,
            version_id=F("document__current_version_id"),
            embedding_model=EMBEDDING_MODEL,
        )
        .values("document_id")
        .distinct()
        .count()
    )
    chunk_count = DocumentEmbedding.objects.filter(
        document__in=docs,
        version_id=F("document__current_version_id"),
        embedding_model=EMBEDDING_MODEL,
    ).count()
    return {
        "model": EMBEDDING_MODEL,
        "dimension": DIMENSION,
        "documents": doc_count,
        "indexed_documents": indexed_docs,
        "missing_documents": max(doc_count - indexed_docs, 0),
        "chunks": chunk_count,
        "generated_at": timezone.now().isoformat(),
    }


def centroid(chunks: list[DocumentEmbedding]) -> tuple[list[float], float]:
    vector = [0.0] * DIMENSION
    used = 0
    for chunk in chunks:
        if not chunk.vector or chunk.magnitude <= 0:
            continue
        for index, value in enumerate(chunk.vector[:DIMENSION]):
            vector[index] += float(value)
        used += 1
    if used:
        vector = [value / used for value in vector]
    magnitude = math.sqrt(sum(value * value for value in vector))
    return vector, magnitude


def cosine(
    left: list[float],
    left_magnitude: float,
    right: list[float],
    right_magnitude: float,
) -> float:
    if left_magnitude <= 0 or right_magnitude <= 0:
        return 0.0
    dot = 0.0
    for a, b in zip(left, right):
        dot += float(a) * float(b)
    return dot / (left_magnitude * right_magnitude)


def snippet_around(text: str, terms: list[str], *, radius: int = 420) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return ""
    folded = fold(cleaned)
    positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
    pos = min(positions) if positions else 0
    start = max(0, pos - radius // 2)
    end = min(len(cleaned), start + radius)
    start = max(0, end - radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(cleaned) else ""
    return f"{prefix}{cleaned[start:end]}{suffix}"


def highlight(text: str, terms: list[str]) -> str:
    safe = escape(text)
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


def fold(value: str) -> str:
    folded = (value or "").lower()
    folded = (
        folded.replace("ä", "a")
        .replace("ö", "o")
        .replace("ü", "u")
        .replace("ß", "ss")
    )
    return re.sub(r"\s+", " ", folded)
