"""Wortgenaue Seiten-Geometrie für das visuelle Dokument-Studio.

Extrahiert pro Seite die Wortkästen (``bbox``) samt Seitenmaßen aus dem
OCR-/Archiv-PDF (PyMuPDF) und legt sie in ``DocumentPageLayout`` ab. Grundlage
für das OCR-Overlay: das Frontend kann Text/Treffer deckungsgleich über die
gerenderte Seite legen und zur Fundstelle springen.

Bewusst robust und weich: Nicht-PDFs, defekte oder textlose Seiten liefern
einfach kein Layout (leer) – nie ein Pipeline-Abbruch. Reine Anzeige-Geometrie,
kein WORM-Original; ein erneuter Lauf ersetzt sie idempotent.
"""
from __future__ import annotations

import os
from pathlib import Path

from celery.exceptions import SoftTimeLimitExceeded

# Deckel gegen entartete PDFs (riesige generierte Wortlisten): pro Seite und
# insgesamt. Für echte Belege großzügig; verhindert nur DB-/JSON-Explosionen.
MAX_WORDS_PER_PAGE = 6000
MAX_WORDS_TOTAL = 60000


def extract_page_layout(path: str | Path) -> list[dict]:
    """Extrahiert Wortkästen pro Seite.

    Liefert ``[{"page_no", "width", "height", "words": [{"t", "bbox"}]}]``.
    ``bbox`` ist ``[x0, y0, x1, y1]`` in PDF-Punkten (Ursprung oben-links). Für
    Nicht-PDFs, fehlende/defekte Dateien oder Seiten ohne erkannte Wörter bleibt
    das Ergebnis leer.
    """
    source = str(path)
    if not source or not os.path.exists(source):
        return []
    if not source.lower().endswith(".pdf"):
        return []

    try:
        import fitz
    except Exception:  # PyMuPDF fehlt – Overlay ist optional
        return []

    pages: list[dict] = []
    total = 0
    try:
        doc = fitz.open(source)
    except SoftTimeLimitExceeded:
        raise  # Soft-Time-Limit nie verschlucken
    except Exception:
        return []

    try:
        for index, page in enumerate(doc):
            if total >= MAX_WORDS_TOTAL:
                break
            try:
                rect = page.rect
                # get_text("words") -> (x0, y0, x1, y1, wort, block, line, word_no)
                raw = page.get_text("words") or []
            except SoftTimeLimitExceeded:
                raise
            except Exception:
                continue
            words: list[dict] = []
            for w in raw:
                if len(w) < 5:
                    continue
                text = str(w[4] or "").strip()
                if not text:
                    continue
                words.append(
                    {
                        "t": text,
                        "bbox": [
                            round(float(w[0]), 2),
                            round(float(w[1]), 2),
                            round(float(w[2]), 2),
                            round(float(w[3]), 2),
                        ],
                    }
                )
                if len(words) >= MAX_WORDS_PER_PAGE:
                    break
            if not words:
                continue
            total += len(words)
            pages.append(
                {
                    "page_no": index + 1,
                    "width": round(float(rect.width), 2),
                    "height": round(float(rect.height), 2),
                    "words": words,
                }
            )
    finally:
        try:
            doc.close()
        except Exception:
            pass

    return pages


def write_page_layout(version, pages: list[dict]) -> int:
    """Ersetzt das Seiten-Layout einer Version atomar im kleinen Maßstab."""
    from documents.models import DocumentPageLayout

    DocumentPageLayout.objects.filter(version=version).delete()
    items = [
        DocumentPageLayout(
            version=version,
            page_no=int(page["page_no"]),
            width=float(page.get("width") or 0.0),
            height=float(page.get("height") or 0.0),
            words=list(page.get("words") or []),
        )
        for page in pages
        if page.get("words")
    ]
    if items:
        DocumentPageLayout.objects.bulk_create(items)
    return len(items)
