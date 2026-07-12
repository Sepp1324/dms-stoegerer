"""Semantischer Index fuer Dokumente – echte Embeddings auf pgvector.

Der Index nutzt lokale fastembed/ONNX-Embeddings (Default ``multilingual-e5-large``,
1024-dim, mehrsprachig, kein torch/API) und speichert je Text-Chunk einen
pgvector-Vektor (``DocumentChunk.embedding``). Aehnlichkeit wird als Cosine-Distance
direkt in Postgres berechnet – skaliert deutlich besser als Python-seitige Schleifen.

Die fachliche API (``sync_document_embeddings``, ``search_documents``,
``similar_documents``, ``embedding_health``) ist bewusst stabil gehalten: Copilot-
Retrieval, Health-Endpoint und die Aehnlichkeitssuche haengen daran und ziehen die
echten Embeddings ohne weitere Verdrahtung mit. Die Praesentations-Helfer
(``snippet_around``/``highlight``) formen die Treffer fuer die UI.
"""
from __future__ import annotations

import logging
import re
from html import escape
from typing import Iterable

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from pgvector.django import CosineDistance

from ai import embeddings
from documents import chunking
from documents.models import Document, DocumentChunk, DocumentVersion

logger = logging.getLogger(__name__)

# Kennzeichnung des aktiven Embedders (echte fastembed/e5-Vektoren). Wird von
# Management-Commands und dem Health-Endpoint als Modellname ausgegeben.
EMBEDDING_MODEL = settings.EMBEDDING_MODEL
DIMENSION = settings.EMBEDDING_DIM
# Mindest-Cosine-Aehnlichkeit fuer einen Treffer (Rauschfilter, ueber Env justierbar).
MIN_SIMILARITY = settings.EMBEDDING_MIN_SIMILARITY

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


def sync_document_embeddings(
    document: Document, *, version: DocumentVersion | None = None
) -> dict:
    """Erzeugt den semantischen Index (pgvector-Chunks) fuer eine Version neu.

    Idempotent: bestehende Chunks der Version werden zuerst entfernt. Ist die
    Embedding-Erzeugung deaktiviert (Tests, Env), bleibt ein vorhandener Index
    unangetastet und es wird nichts geladen. Fehler beim Modell brechen die
    Pipeline nicht – sie werden geloggt und als Status zurueckgegeben.
    """
    version = version or document.current_version
    if version is None:
        return {"status": "missing_version", "created": 0, "deleted": 0}
    if not embeddings.enabled():
        return {"status": "disabled", "created": 0, "deleted": 0, "model": EMBEDDING_MODEL}

    texts = chunking.chunk_text(version.ocr_text or "")
    deleted, _ = DocumentChunk.objects.filter(version=version).delete()
    if not texts:
        return {
            "status": "empty",
            "created": 0,
            "deleted": deleted,
            "version": version.id,
            "model": EMBEDDING_MODEL,
        }

    try:
        vectors = embeddings.embed_passages(texts)
    except Exception:  # noqa: BLE001 – Modellfehler darf die Pipeline nicht kippen
        logger.exception("Embedding fehlgeschlagen für Version %s", version.id)
        return {
            "status": "error",
            "created": 0,
            "deleted": deleted,
            "version": version.id,
            "model": EMBEDDING_MODEL,
        }

    DocumentChunk.objects.bulk_create(
        [
            DocumentChunk(
                document=document,
                version=version,
                chunk_index=index,
                text=text,
                embedding=vector,
            )
            for index, (text, vector) in enumerate(zip(texts, vectors))
        ]
    )
    return {
        "status": "indexed",
        "created": len(texts),
        "deleted": deleted,
        "version": version.id,
        "model": EMBEDDING_MODEL,
    }


def search_documents(
    question: str,
    documents: Iterable[Document],
    *,
    limit: int = 8,
    min_score: float | None = None,
) -> list[dict]:
    """Sucht semantisch in einem bereits owner-gescopten Dokumentbestand."""
    min_score = MIN_SIMILARITY if min_score is None else min_score
    doc_ids = [doc.id for doc in documents]
    if not doc_ids or not embeddings.enabled():
        return []

    try:
        query_vector = embeddings.embed_query(question)
    except Exception:  # noqa: BLE001 – ohne Modell einfach keine semantischen Treffer
        logger.exception("Query-Embedding fehlgeschlagen")
        return []

    # Cosine-Distance in Postgres; nur Chunks der jeweils aktuellen Version. Wir
    # holen mehr als ``limit`` Zeilen, weil pro Dokument nur der beste Chunk zaehlt.
    rows = (
        DocumentChunk.objects.select_related("document", "document__folder")
        .filter(
            document_id__in=doc_ids,
            version_id=F("document__current_version_id"),
            embedding__isnull=False,
        )
        .annotate(distance=CosineDistance("embedding", query_vector))
        .order_by("distance")[: max(limit * 5, limit)]
    )

    terms = tokenize(question)
    out: list[dict] = []
    seen: set[int] = set()
    for chunk in rows:
        score = 1.0 - float(chunk.distance)
        if score < min_score:
            continue
        if chunk.document_id in seen:
            continue
        seen.add(chunk.document_id)
        snippet = snippet_around(chunk.text, terms)
        out.append(
            {
                "document": chunk.document_id,
                "document_title": chunk.document.title,
                "folder_path": chunk.document.folder.full_path
                if chunk.document.folder_id
                else None,
                "page": None,
                "snippet": snippet,
                "snippet_html": highlight(snippet, terms),
                "score": round(score, 4),
                "reason": "Semantischer Treffer",
                "source_type": "semantic",
                "matched_terms": terms[:8],
                "semantic_model": EMBEDDING_MODEL,
                "entities": [],
                "contract": None,
                "case_file": None,
            }
        )
        if len(out) >= limit:
            break
    return out


def similar_documents(
    document: Document, visible_documents: Iterable[Document], *, limit: int = 6
) -> list[dict]:
    """Findet aehnliche sichtbare Dokumente anhand der aktuellen Version."""
    if not embeddings.enabled():
        return []

    base_vectors = list(
        DocumentChunk.objects.filter(
            document=document,
            version=document.current_version,
            embedding__isnull=False,
        ).values_list("embedding", flat=True)
    )
    if not base_vectors:
        return []

    visible_ids = [doc.id for doc in visible_documents if doc.id != document.id]
    if not visible_ids:
        return []

    import numpy as np

    centroid = np.mean(np.asarray(base_vectors, dtype=float), axis=0).tolist()

    rows = (
        DocumentChunk.objects.select_related("document", "document__folder")
        .filter(
            document_id__in=visible_ids,
            version_id=F("document__current_version_id"),
            embedding__isnull=False,
        )
        .annotate(distance=CosineDistance("embedding", centroid))
        .order_by("distance")[: max(limit * 5, limit)]
    )

    results: list[dict] = []
    seen: set[int] = set()
    for chunk in rows:
        score = 1.0 - float(chunk.distance)
        if score < MIN_SIMILARITY or chunk.document_id in seen:
            continue
        seen.add(chunk.document_id)
        snippet = snippet_around(chunk.text, [])
        results.append(
            {
                "document": chunk.document_id,
                "document_title": chunk.document.title,
                "folder_path": chunk.document.folder.full_path
                if chunk.document.folder_id
                else None,
                "page": None,
                "score": round(score, 4),
                "reason": "Ähnlicher Inhalt",
                "snippet": snippet,
                "snippet_html": escape(snippet),
            }
        )
        if len(results) >= limit:
            break
    return results


def embedding_health(visible_documents: Iterable[Document] | None = None) -> dict:
    """Kompakter Betriebsstatus des semantischen Index."""
    docs = visible_documents if visible_documents is not None else Document.objects.all()
    docs = docs.exclude(current_version__isnull=True)
    doc_count = docs.count()
    current_chunks = DocumentChunk.objects.filter(
        document__in=docs,
        version_id=F("document__current_version_id"),
        embedding__isnull=False,
    )
    indexed_docs = current_chunks.values("document_id").distinct().count()
    chunk_count = current_chunks.count()
    return {
        "model": EMBEDDING_MODEL,
        "dimension": DIMENSION,
        "enabled": embeddings.enabled(),
        "documents": doc_count,
        "indexed_documents": indexed_docs,
        "missing_documents": max(doc_count - indexed_docs, 0),
        "chunks": chunk_count,
        "generated_at": timezone.now().isoformat(),
    }


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
