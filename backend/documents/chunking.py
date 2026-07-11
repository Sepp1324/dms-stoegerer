"""Text-Chunking für Embeddings: OCR-Text in überlappende Abschnitte teilen.

Bewusst simpel und deterministisch (zeichenbasiert mit Überlappung) – kein
Tokenizer nötig, robust für beliebige Sprachen. Die Überlappung sorgt dafür, dass
Sätze/Begriffe an Chunk-Grenzen nicht verloren gehen.
"""
from __future__ import annotations


def chunk_text(
    text: str | None, *, max_chars: int = 1000, overlap: int = 150
) -> list[str]:
    """Zerlegt Text in überlappende Chunks (max. ``max_chars``, ``overlap`` Zeichen)."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    step = max(1, max_chars - overlap)
    while start < n:
        end = min(start + max_chars, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start += step
    return chunks
