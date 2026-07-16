"""Hybride Suche: PostgreSQL-Volltext (FTS) + semantische Embeddings fusioniert.

Die Volltextsuche ist präzise bei exakten Begriffen/Namen, die semantische Suche
trifft die Bedeutung (auch ohne wörtliche Übereinstimmung). Statt zwei getrennter
Ergebnislisten führen wir beide per **Reciprocal Rank Fusion (RRF)** zu einem
Ranking zusammen: jedes Dokument bekommt aus jeder Liste ``1/(k + rang)`` addiert.
RRF ist robust (keine Score-Normalisierung nötig) und belohnt Dokumente, die in
BEIDEN Verfahren auftauchen.
"""
from __future__ import annotations

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

from documents.models import Document
from documents.services import semantic_index

RRF_K = 60


def _fts_ranked_ids(visible_qs, query: str, *, limit: int) -> list[int]:
    """Beste Volltext-Treffer (gewichtet wie die Listensuche), dedupliziert."""
    vector = (
        SearchVector("title", weight="A", config="german")
        + SearchVector("correspondent__name", weight="A", config="german")
        + SearchVector("document_type__name", weight="B", config="german")
        + SearchVector("tags__name", weight="B", config="german")
        + SearchVector("mail_subject", weight="B", config="german")
        + SearchVector("mail_sender", weight="B", config="german")
        + SearchVector("note", weight="B", config="german")
        + SearchVector("current_version__ocr_text", weight="D", config="german")
    )
    search_query = SearchQuery(query, config="german")
    rows = (
        visible_qs.annotate(rank=SearchRank(vector, search_query))
        .filter(rank__gt=0)
        .order_by("-rank", "-added_at")
        .values_list("id", flat=True)
    )
    ordered: list[int] = []
    seen: set[int] = set()
    for doc_id in rows:  # Joins (tags) können IDs mehrfach liefern → dedupe
        if doc_id in seen:
            continue
        seen.add(doc_id)
        ordered.append(doc_id)
        if len(ordered) >= limit:
            break
    return ordered


def hybrid_search(visible_qs, query: str, *, limit: int = 10) -> list[dict]:
    """Fusioniert Volltext- und Semantik-Treffer (RRF) zu einem Ranking.

    ``visible_qs`` muss bereits owner-/haushalts-gescoped sein. Gibt Ergebnis-Dicts
    zurück (document, title, folder_path, snippet_html, score, sources, page).
    """
    query = (query or "").strip()
    if not query:
        return []

    pool = max(limit * 3, limit)
    fts_ids = _fts_ranked_ids(visible_qs, query, limit=pool)

    semantic_hits = semantic_index.search_documents(query, visible_qs, limit=pool)
    sem_by_doc = {hit["document"]: hit for hit in semantic_hits}

    # --- Reciprocal Rank Fusion ---
    scores: dict[int, float] = {}
    sources: dict[int, set] = {}
    for rank, doc_id in enumerate(fts_ids):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        sources.setdefault(doc_id, set()).add("fts")
    for rank, hit in enumerate(semantic_hits):
        doc_id = hit["document"]
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        sources.setdefault(doc_id, set()).add("semantic")

    ranked_ids = sorted(scores, key=scores.get, reverse=True)[:limit]
    if not ranked_ids:
        return []

    docs = {
        doc.id: doc
        for doc in Document.objects.select_related("folder", "current_version").filter(
            id__in=ranked_ids
        )
    }
    terms = semantic_index.tokenize(query)
    results: list[dict] = []
    for doc_id in ranked_ids:
        doc = docs.get(doc_id)
        if doc is None:
            continue
        sem_hit = sem_by_doc.get(doc_id)
        if sem_hit and sem_hit.get("snippet_html"):
            snippet_html = sem_hit["snippet_html"]
            snippet = sem_hit.get("snippet", "")
        else:
            ocr = doc.current_version.ocr_text if doc.current_version_id else ""
            snippet = semantic_index.snippet_around(ocr, terms)
            snippet_html = semantic_index.highlight(snippet, terms)
        results.append(
            {
                "document": doc_id,
                "document_title": doc.title,
                "folder_path": doc.folder.full_path if doc.folder_id else None,
                "page": None,
                "snippet": snippet,
                "snippet_html": snippet_html,
                "score": round(scores[doc_id], 5),
                "sources": sorted(sources.get(doc_id, set())),
            }
        )
    return results
