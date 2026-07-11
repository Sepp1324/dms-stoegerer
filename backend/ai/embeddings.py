"""Lokale Text-Embeddings (fastembed / ONNX) für die semantische Suche.

Kein torch, kein externer API-Call: fastembed lädt ein mehrsprachiges ONNX-Modell
(Default ``intfloat/multilingual-e5-large``, 1024-dim) und rechnet die Embeddings
auf der CPU. Das Modell wird einmal geladen (Lazy-Singleton) und unter
``settings.EMBEDDING_CACHE_DIR`` (persistentes /data-PVC) gecached, damit es nicht
bei jedem Worker-Neustart neu geladen wird.

e5-Modelle erwarten Prefixe: ``passage:`` für Dokument-Chunks, ``query:`` für
Suchanfragen – das macht die Ähnlichkeit deutlich besser.
"""
from __future__ import annotations

import logging
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

_model = None
_lock = threading.Lock()


def enabled() -> bool:
    return bool(getattr(settings, "EMBEDDING_ENABLED", True))


def dim() -> int:
    return int(getattr(settings, "EMBEDDING_DIM", 1024))


def _get_model():
    """Lazy-Singleton des fastembed-Modells (thread-safe)."""
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding

            name = getattr(
                settings, "EMBEDDING_MODEL", "intfloat/multilingual-e5-large"
            )
            cache = getattr(settings, "EMBEDDING_CACHE_DIR", None)
            _model = TextEmbedding(
                model_name=name,
                cache_dir=str(cache) if cache else None,
            )
            logger.info("Embedding-Modell geladen: %s", name)
    return _model


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Embeddings für Dokument-Chunks (mit e5-``passage:``-Prefix)."""
    model = _get_model()
    prefixed = [f"passage: {t}" for t in texts]
    return [list(map(float, vec)) for vec in model.embed(prefixed)]


def embed_query(text: str) -> list[float]:
    """Embedding einer Suchanfrage (mit e5-``query:``-Prefix)."""
    model = _get_model()
    vec = next(iter(model.embed([f"query: {text}"])))
    return list(map(float, vec))
