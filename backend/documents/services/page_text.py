"""Seitengenauer Textindex für Copilot-Quellen."""
from __future__ import annotations

import os
from pathlib import Path


def extract_page_texts(path: str | Path, *, fallback_text: str = "") -> list[dict]:
    """Extrahiert Text pro Seite.

    PDFs werden über PyMuPDF seitenweise gelesen. Für Nicht-PDFs oder defekte
    Dateien fällt der Service auf eine einzelne Seite mit dem vorhandenen
    OCR-Gesamttext zurück.
    """
    source = str(path)
    if not source or not os.path.exists(source):
        return _fallback(fallback_text)

    if source.lower().endswith(".pdf"):
        try:
            import fitz

            doc = fitz.open(source)
            pages = [
                {"page_no": index + 1, "text": page.get_text() or ""}
                for index, page in enumerate(doc)
            ]
            pages = [page for page in pages if page["text"].strip()]
            if pages:
                return pages
        except Exception:
            pass

    return _fallback(fallback_text)


def write_page_texts(version, pages: list[dict]) -> int:
    """Ersetzt den Seitentextindex einer Version atomar im kleinen Maßstab."""
    from documents.models import DocumentPageText

    DocumentPageText.objects.filter(version=version).delete()
    items = [
        DocumentPageText(
            version=version,
            page_no=int(page["page_no"]),
            text=str(page.get("text") or ""),
        )
        for page in pages
        if str(page.get("text") or "").strip()
    ]
    if items:
        DocumentPageText.objects.bulk_create(items)
    return len(items)


def _fallback(text: str) -> list[dict]:
    cleaned = (text or "").strip()
    return [{"page_no": 1, "text": cleaned}] if cleaned else []
