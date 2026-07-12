"""Auto-Ablage: schlägt Ordner/Tags/Korrespondent/Typ per kNN über Embeddings vor.

Idee: „Dokumente, die diesem inhaltlich am ähnlichsten sind, lagen zu 90 % in
Ordner *Versicherung/KFZ* und trugen Tag *Auto*." Der Service holt die nächsten
Nachbarn (Cosine-Distance über die pgvector-Chunks der aktuellen Versionen),
gewichtet deren vorhandene Ablage-Metadaten nach Ähnlichkeit und leitet daraus
Vorschläge mit Confidence ab. Rein lokal – kein LLM/API-Key nötig.
"""
from __future__ import annotations

import logging

from django.db.models import F

from ai import embeddings
from documents.models import Document, DocumentChunk

logger = logging.getLogger(__name__)

# Wie viele ähnliche Nachbar-Dokumente fließen in die Abstimmung ein.
K_NEIGHBORS = 12
# Nachbarn unter dieser Cosine-Ähnlichkeit sind zu unähnlich, um zu „wählen".
MIN_NEIGHBOR_SIMILARITY = 0.55
# Ein Feldwert wird vorgeschlagen, wenn sein Anteil an der Ähnlichkeits-Masse
# (Summe der Nachbar-Scores) mindestens so groß ist.
FIELD_CONFIDENCE_THRESHOLD = 0.34
TAG_CONFIDENCE_THRESHOLD = 0.30
MAX_TAGS = 6


def suggest_filing(document: Document, visible_documents, *, k: int = K_NEIGHBORS) -> dict:
    """Leitet Ablage-Vorschläge aus den ähnlichsten sichtbaren Dokumenten ab."""
    if not embeddings.enabled():
        return {"status": "disabled"}

    base_vectors = list(
        DocumentChunk.objects.filter(
            document=document,
            version=document.current_version,
            embedding__isnull=False,
        ).values_list("embedding", flat=True)
    )
    if not base_vectors:
        return {"status": "no_embeddings"}

    visible_ids = [doc.id for doc in visible_documents if doc.id != document.id]
    if not visible_ids:
        return {"status": "no_neighbors", "neighbors": []}

    import numpy as np
    from pgvector.django import CosineDistance

    centroid = np.mean(np.asarray(base_vectors, dtype=float), axis=0).tolist()

    rows = (
        DocumentChunk.objects.select_related(
            "document",
            "document__folder",
            "document__correspondent",
            "document__document_type",
        )
        .prefetch_related("document__tags")
        .filter(
            document_id__in=visible_ids,
            version_id=F("document__current_version_id"),
            embedding__isnull=False,
        )
        .annotate(distance=CosineDistance("embedding", centroid))
        .order_by("distance")[: max(k * 6, k)]
    )

    # Pro Nachbar-Dokument nur der beste (nächste) Chunk zählt.
    neighbors: list[tuple[float, Document]] = []
    seen: set[int] = set()
    for chunk in rows:
        score = 1.0 - float(chunk.distance)
        if score < MIN_NEIGHBOR_SIMILARITY or chunk.document_id in seen:
            continue
        seen.add(chunk.document_id)
        neighbors.append((score, chunk.document))
        if len(neighbors) >= k:
            break

    if not neighbors:
        return {"status": "no_neighbors", "neighbors": []}

    total = sum(score for score, _ in neighbors) or 1.0

    def vote(value_of, label_of) -> dict | None:
        tally: dict[int, float] = {}
        labels: dict[int, str] = {}
        for score, doc in neighbors:
            value_id = value_of(doc)
            if not value_id:
                continue
            tally[value_id] = tally.get(value_id, 0.0) + score
            labels[value_id] = label_of(doc)
        if not tally:
            return None
        best_id = max(tally, key=tally.get)
        confidence = tally[best_id] / total
        if confidence < FIELD_CONFIDENCE_THRESHOLD:
            return None
        return {"id": best_id, "label": labels[best_id], "confidence": round(confidence, 3)}

    folder = vote(
        lambda d: d.folder_id,
        lambda d: d.folder.full_path if d.folder_id else "",
    )
    correspondent = vote(
        lambda d: d.correspondent_id,
        lambda d: d.correspondent.name if d.correspondent_id else "",
    )
    document_type = vote(
        lambda d: d.document_type_id,
        lambda d: d.document_type.name if d.document_type_id else "",
    )

    tag_tally: dict[int, float] = {}
    tag_labels: dict[int, str] = {}
    for score, doc in neighbors:
        for tag in doc.tags.all():
            tag_tally[tag.id] = tag_tally.get(tag.id, 0.0) + score
            tag_labels[tag.id] = tag.name
    tags = [
        {"id": tid, "name": tag_labels[tid], "confidence": round(weight / total, 3)}
        for tid, weight in tag_tally.items()
        if weight / total >= TAG_CONFIDENCE_THRESHOLD
    ]
    tags.sort(key=lambda t: t["confidence"], reverse=True)
    tags = tags[:MAX_TAGS]

    current_tag_ids = list(document.tags.values_list("id", flat=True))
    return {
        "status": "ok",
        "folder": folder,
        "correspondent": correspondent,
        "document_type": document_type,
        "tags": tags,
        "current": {
            "folder": document.folder_id,
            "correspondent": document.correspondent_id,
            "document_type": document.document_type_id,
            "tags": current_tag_ids,
        },
        "neighbors": [
            {"document": doc.id, "title": doc.title, "score": round(score, 3)}
            for score, doc in neighbors
        ],
    }


def apply_filing(document: Document, suggestion: dict, *, fields: list[str] | None = None) -> list[str]:
    """Wendet Vorschläge an. FK-Felder nur, wenn leer (füllt Lücken, überschreibt
    keine manuelle Wahl); Tags werden ergänzt (Union). Gibt die geänderten Felder
    zurück. ``fields`` beschränkt optional auf ausgewählte Felder.
    """
    wanted = fields if isinstance(fields, list) else ["folder", "correspondent", "document_type", "tags"]
    changed: list[str] = []

    fk_map = {
        "folder": "folder_id",
        "correspondent": "correspondent_id",
        "document_type": "document_type_id",
    }
    for field, attr in fk_map.items():
        if field not in wanted:
            continue
        proposal = suggestion.get(field)
        if not proposal or getattr(document, attr):
            continue  # kein Vorschlag oder Feld bereits gesetzt → nicht anfassen
        setattr(document, attr, proposal["id"])
        changed.append(field)

    fk_changed = [f for f in ("folder", "correspondent", "document_type") if f in changed]
    if fk_changed:
        document.save(update_fields=fk_changed)

    if "tags" in wanted and suggestion.get("tags"):
        existing = set(document.tags.values_list("id", flat=True))
        to_add = [t["id"] for t in suggestion["tags"] if t["id"] not in existing]
        if to_add:
            document.tags.add(*to_add)
            changed.append("tags")

    return changed
