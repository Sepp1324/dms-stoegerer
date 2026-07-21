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

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

logger = logging.getLogger(__name__)

_model = None
# Gecachter Ladefehler: Scheitert das Modell-Laden (z. B. fehlende model.onnx),
# wird der Fehler EINMAL geloggt und gemerkt – Folgeaufrufe fehlschlagen dann
# knapp, ohne das (teure) Laden erneut zu versuchen und pro Dokument einen
# vollen Traceback zu produzieren (Lehre aus dem 0.8.0-Modell-Incident).
_load_error: str | None = None
_lock = threading.Lock()


class EmbeddingModelUnavailable(RuntimeError):
    """Das Embedding-Modell konnte nicht geladen werden (Ladefehler gecacht)."""


def enabled() -> bool:
    return bool(getattr(settings, "EMBEDDING_ENABLED", True))


def dim() -> int:
    return int(getattr(settings, "EMBEDDING_DIM", 1024))


def _get_model():
    """Lazy-Singleton des fastembed-Modells (thread-safe).

    Bei einem Ladefehler wird dieser gecacht: die erste Ausnahme wird einmal als
    klare Fehlermeldung geloggt, danach werfen weitere Aufrufe sofort
    ``EmbeddingModelUnavailable`` – kein erneuter Ladeversuch, keine
    Traceback-Flut über einen ganzen Reindex-Lauf.
    """
    global _model, _load_error
    if _model is not None:
        return _model
    if _load_error is not None:
        raise EmbeddingModelUnavailable(_load_error)
    with _lock:
        if _model is not None:
            return _model
        if _load_error is not None:
            raise EmbeddingModelUnavailable(_load_error)
        name = getattr(settings, "EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
        cache = getattr(settings, "EMBEDDING_CACHE_DIR", None)
        # onnxruntime-Threads deckeln (Speicher-Arenen) -> kein OOMKill mehr.
        kwargs = {"model_name": name, "cache_dir": str(cache) if cache else None}
        threads = int(getattr(settings, "EMBEDDING_THREADS", 0) or 0)
        if threads > 0:
            kwargs["threads"] = threads
        try:
            from fastembed import TextEmbedding

            _model = TextEmbedding(**kwargs)
        except SoftTimeLimitExceeded:
            raise  # Timeout NICHT als permanenten Ladefehler cachen
        except Exception as exc:  # noqa: BLE001 – Ladefehler einmal klar melden + cachen
            _load_error = (
                f"Embedding-Modell '{name}' konnte nicht geladen werden "
                f"(cache_dir={cache}): {exc}"
            )
            logger.error(_load_error)
            raise EmbeddingModelUnavailable(_load_error) from exc
        logger.info("Embedding-Modell geladen: %s", name)
    return _model


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Embeddings für Dokument-Chunks (mit modell-spezifischem Passage-Prefix)."""
    model = _get_model()
    prefix = getattr(settings, "EMBEDDING_PASSAGE_PREFIX", "")
    prefixed = [f"{prefix}{t}" for t in texts]
    return [list(map(float, vec)) for vec in model.embed(prefixed)]


def embed_query(text: str) -> list[float]:
    """Embedding einer Suchanfrage (mit modell-spezifischem Query-Prefix)."""
    model = _get_model()
    prefix = getattr(settings, "EMBEDDING_QUERY_PREFIX", "")
    vec = next(iter(model.embed([f"{prefix}{text}"])))
    return list(map(float, vec))
