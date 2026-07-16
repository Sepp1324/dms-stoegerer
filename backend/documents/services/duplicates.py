"""Dubletten- & Versionserkennung über die semantischen Embeddings.

Der Ingest blockt bereits *byte-identische* Uploads (sha256). Diese Erkennung
findet die *inhaltlichen* Beinah-Duplikate, die dabei durchrutschen: derselbe Beleg
zweimal eingescannt (leicht anderes Bild/OCR) oder eine neuere Fassung desselben
Dokuments. Grundlage ist die Cosine-Ähnlichkeit der Dokument-Centroide (pgvector).

Klassifikation:
- score >= STRONG_THRESHOLD  → "duplicate" (praktisch dasselbe Dokument)
- score >= THRESHOLD          → "version"   (sehr ähnlich, evtl. neue Fassung)
"""
from __future__ import annotations

import logging
import re

from django.conf import settings
from django.db.models import F

from ai import embeddings
from documents.models import Document, DocumentChunk

logger = logging.getLogger(__name__)

THRESHOLD = float(getattr(settings, "DUPLICATE_THRESHOLD", 0.93))
STRONG_THRESHOLD = float(getattr(settings, "DUPLICATE_STRONG_THRESHOLD", 0.97))
LEXICAL_STRONG = float(getattr(settings, "DUPLICATE_LEXICAL_STRONG", 0.80))

_TOKEN_RE = re.compile(r"[a-z0-9äöüß]{2,}")


def _normalized_tokens(text: str | None) -> set:
    """Menge normalisierter Tokens (inkl. Zahlen – Rechnungsnummern/Beträge zählen)."""
    return set(_TOKEN_RE.findall((text or "").lower()))


def _lexical_similarity(tokens_a: set, tokens_b: set) -> float:
    """Jaccard-Ähnlichkeit zweier Tokenmengen (0..1)."""
    if not tokens_a or not tokens_b:
        return 0.0
    union = len(tokens_a | tokens_b)
    return len(tokens_a & tokens_b) / union if union else 0.0


def _classify(score: float, lexical: float) -> str:
    """"duplicate" nur bei hohem Cosine UND nahezu identischem Text; sonst "version".

    Trennt echte Doppel-Scans (Text fast gleich) von wiederkehrenden, aber
    verschiedenen Dokumenten gleicher Vorlage (semantisch fast gleich, Text weicht
    in Nummern/Daten/Beträgen ab).
    """
    if score >= STRONG_THRESHOLD and lexical >= LEXICAL_STRONG:
        return "duplicate"
    return "version"


def _centroid(document: Document) -> list[float] | None:
    vectors = list(
        DocumentChunk.objects.filter(
            document=document,
            version=document.current_version,
            embedding__isnull=False,
        ).values_list("embedding", flat=True)
    )
    if not vectors:
        return None
    import numpy as np

    return np.mean(np.asarray(vectors, dtype=float), axis=0).tolist()


def find_duplicates(
    document: Document,
    visible_documents,
    *,
    threshold: float | None = None,
    limit: int = 10,
) -> dict:
    """Findet inhaltliche Beinah-Duplikate eines Dokuments im sichtbaren Bestand."""
    threshold = THRESHOLD if threshold is None else threshold
    if not embeddings.enabled():
        return {"status": "disabled", "results": []}

    centroid = _centroid(document)
    if centroid is None:
        return {"status": "no_embeddings", "results": []}

    visible_ids = [doc.id for doc in visible_documents if doc.id != document.id]
    if not visible_ids:
        return {"status": "ok", "threshold": threshold, "results": []}

    from pgvector.django import CosineDistance

    rows = (
        DocumentChunk.objects.select_related("document", "document__current_version")
        .filter(
            document_id__in=visible_ids,
            version_id=F("document__current_version_id"),
            embedding__isnull=False,
            document__superseded_by__isnull=True,  # bereits zusammengeführte ausblenden
        )
        .annotate(distance=CosineDistance("embedding", centroid))
        .order_by("distance")[: max(limit * 6, limit)]
    )

    base_tokens = _normalized_tokens(
        document.current_version.ocr_text if document.current_version_id else ""
    )

    results: list[dict] = []
    seen: set[int] = set()
    for chunk in rows:
        score = 1.0 - float(chunk.distance)
        if score < threshold or chunk.document_id in seen:
            continue
        seen.add(chunk.document_id)
        doc = chunk.document
        cand_text = doc.current_version.ocr_text if doc.current_version_id else ""
        lexical = _lexical_similarity(base_tokens, _normalized_tokens(cand_text))
        results.append(
            {
                "document": doc.id,
                "title": doc.title,
                "score": round(score, 4),
                "lexical": round(lexical, 3),
                "kind": _classify(score, lexical),
                "added_at": doc.added_at.isoformat() if doc.added_at else None,
                "sha256": doc.current_version.sha256 if doc.current_version_id else None,
            }
        )
        if len(results) >= limit:
            break
    return {"status": "ok", "threshold": threshold, "results": results}


def duplicate_report(
    visible_documents,
    *,
    threshold: float | None = None,
    max_documents: int = 500,
    limit: int = 100,
) -> dict:
    """Korpus-Report: findet Paare inhaltlicher Beinah-Duplikate im Bestand.

    Für jedes Dokument wird der nächste Nachbar oberhalb der Schwelle gesucht und
    zu ungerichteten Paaren zusammengefasst (deduped, nach Score sortiert). Für den
    Familien-Korpus performant genug; ``max_documents`` deckelt sehr große Bestände.
    """
    threshold = THRESHOLD if threshold is None else threshold
    if not embeddings.enabled():
        return {"status": "disabled", "pairs": []}

    docs = list(visible_documents)[:max_documents]
    titles = {doc.id: doc.title for doc in docs}

    pairs: dict[frozenset[int], dict] = {}
    for doc in docs:
        found = find_duplicates(doc, docs, threshold=threshold, limit=3)
        for hit in found["results"]:
            key = frozenset((doc.id, hit["document"]))
            if len(key) < 2:
                continue
            if key not in pairs or hit["score"] > pairs[key]["score"]:
                pairs[key] = {
                    "a": doc.id,
                    "a_title": titles.get(doc.id, ""),
                    "b": hit["document"],
                    "b_title": titles.get(hit["document"], hit["title"]),
                    "score": hit["score"],
                    "lexical": hit.get("lexical"),
                    "kind": hit["kind"],
                }

    ordered = sorted(pairs.values(), key=lambda p: p["score"], reverse=True)[:limit]
    return {"status": "ok", "threshold": threshold, "count": len(ordered), "pairs": ordered}
