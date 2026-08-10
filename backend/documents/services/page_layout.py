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
import unicodedata
from pathlib import Path

from celery.exceptions import SoftTimeLimitExceeded

# Deckel gegen entartete PDFs (riesige generierte Wortlisten): pro Seite und
# insgesamt. Für echte Belege großzügig; verhindert nur DB-/JSON-Explosionen.
MAX_WORDS_PER_PAGE = 6000
MAX_WORDS_TOTAL = 60000

# Deckel für die In-Dokument-Suche: begrenzt die Trefferantwort (Payload/Navigation).
MAX_SEARCH_HITS = 500
# Wie viele aufeinanderfolgende Wörter eine gesuchte Wortfolge höchstens umfassen darf.
_SEARCH_WINDOW = 12


def normalize_search(s: str) -> str:
    """Normalisierung für die In-Dokument-Suche.

    Deckungsgleich mit ``normalizeText`` im Frontend: Diakritika entfernen
    (Müller→muller) und Kleinschreibung. So trifft die serverseitige Suche exakt
    dasselbe wie die frühere clientseitige – reines Teilstring-Matching, kein Regex.
    """
    decomposed = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _search_norm(s: str) -> str:
    """Normalisierung fürs Suchen: Diakritika + JEDER Whitespace entfernt, lower.

    Whitespace muss weg, weil eine gesuchte Wortfolge (``Wien Energie``, eine in
    Blöcke getrennte IBAN/Vertragsnummer) im OCR über MEHRERE Wörter verteilt ist;
    verglichen wird die zusammengezogene Form.
    """
    return "".join(normalize_search(s).split())


def search_layout(version, query: str, *, limit: int = MAX_SEARCH_HITS):
    """Sucht ``query`` seitenübergreifend in den Wortkästen einer Version.

    Liefert ``(matches, truncated)``. Jeder Treffer trägt Seite, Seitenmaße und
    einen (ggf. über mehrere Wörter zusammengeführten) Wortkasten, damit das
    Frontend ohne Zweitabfrage dorthin springen und den Treffer deckungsgleich
    markieren kann. Matching ist diakritika-/case-tolerant UND wortfolgen-fähig:
    ``Wien Energie`` trifft die Wortfolge ``Wien`` + ``Energie``, eine in Blöcke
    getrennte IBAN/Vertragsnummer ebenso. Bei ``limit`` wird ``truncated=True``.
    """
    target = _search_norm(query.strip())
    if not target:
        return [], False
    matches: list[dict] = []
    truncated = False
    # Reihenfolge = page_layouts.Meta.ordering (version_id, page_no); innerhalb der
    # Seite Wortreihenfolge → Treffer sind natürlich dokumentweit sortiert.
    for layout in version.page_layouts.all().iterator():
        words = layout.words or []
        n = len(words)
        i = 0
        while i < n:
            if len(matches) >= limit:
                truncated = True
                break
            acc = ""
            hit_end = -1
            # Fenster aufeinanderfolgender Wörter zusammenziehen, bis der Suchbegriff
            # enthalten ist (max. _SEARCH_WINDOW Wörter).
            for k in range(i, min(i + _SEARCH_WINDOW, n)):
                acc += _search_norm(words[k].get("t") or "")
                if target in acc:
                    hit_end = k
                    break
            if hit_end >= 0:
                # Fenster nach LINKS trimmen: führende Wörter weglassen, die nicht zum
                # Treffer gehören (sonst würde „betrag" ab „Rechnung Betrag" matchen und
                # eine zu große Box liefern). Kleinstes Fenster [start..hit_end], das den
                # Begriff noch enthält.
                start = i
                while start < hit_end:
                    sub = "".join(
                        _search_norm(words[m].get("t") or "")
                        for m in range(start + 1, hit_end + 1)
                    )
                    if target in sub:
                        start += 1
                    else:
                        break
                boxes = [
                    [float(b) for b in words[m]["bbox"][:4]]
                    for m in range(start, hit_end + 1)
                    if words[m].get("bbox") and len(words[m]["bbox"]) >= 4
                ]
                if boxes:
                    matches.append(
                        {
                            "page_no": layout.page_no,
                            "width": layout.width,
                            "height": layout.height,
                            "bbox": _union_bbox(boxes),
                            "t": " ".join(
                                str(words[m].get("t") or "")
                                for m in range(start, hit_end + 1)
                            ).strip(),
                        }
                    )
                i = hit_end + 1  # weiter HINTER dem Treffer (keine Überlappung)
            else:
                i += 1
        if truncated:
            break
    return matches, truncated


def _union_bbox(boxes: list[list[float]]) -> list[float]:
    return [
        round(min(b[0] for b in boxes), 2),
        round(min(b[1] for b in boxes), 2),
        round(max(b[2] for b in boxes), 2),
        round(max(b[3] for b in boxes), 2),
    ]


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
                # ``page.rect`` liefert bereits die ANZEIGE-Maße: bei /Rotate 90/270
                # sind Breite/Höhe vertauscht – deckungsgleich mit dem, was pdf.js
                # (react-pdf) rendert.
                rect = page.rect
                # ``get_text("words")`` liefert die Kästen dagegen im UN-rotierten
                # System. Damit die Boxen zu den rotierten Anzeige-Maßen passen, wird
                # jeder Kasten mit der Rotationsmatrix ins Anzeige-System überführt
                # (bei 0° ist das die Identität → kein Effekt).
                rmat = page.rotation_matrix
                # get_text("words") -> (x0, y0, x1, y1, wort, block, line, word_no)
                raw = page.get_text("words") or []
            except SoftTimeLimitExceeded:
                raise
            except Exception:
                continue
            # Diese Seite darf höchstens so viele Wörter beitragen, dass das
            # GESAMT-Limit nicht überschritten wird (der Seiten-Deckel greift nur
            # zusätzlich). Ohne das ``MAX_WORDS_TOTAL - total`` könnte eine Seite bei
            # z. B. 59.999 bereits vergebenen Wörtern noch bis MAX_WORDS_PER_PAGE
            # drauflegen und das Gesamtlimit reißen.
            page_cap = min(MAX_WORDS_PER_PAGE, MAX_WORDS_TOTAL - total)
            words: list[dict] = []
            for w in raw:
                if len(w) < 5:
                    continue
                text = str(w[4] or "").strip()
                if not text:
                    continue
                box = fitz.Rect(w[0], w[1], w[2], w[3]) * rmat
                box.normalize()  # Rotation kann Ecken tauschen → x0<=x1, y0<=y1
                words.append(
                    {
                        "t": text,
                        "bbox": [
                            round(float(box.x0), 2),
                            round(float(box.y0), 2),
                            round(float(box.x1), 2),
                            round(float(box.y1), 2),
                        ],
                    }
                )
                if len(words) >= page_cap:
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
    """Ersetzt das Seiten-Layout einer Version atomar im kleinen Maßstab.

    Delete + bulk_create laufen in EINER Transaktion: scheitert der Insert (den die
    Pipeline weich abfängt), darf nicht das alte Layout gelöscht zurückbleiben.
    Entweder das neue Layout steht vollständig, oder das alte bleibt unangetastet.
    """
    from django.db import transaction

    from documents.models import DocumentPageLayout

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
    with transaction.atomic():
        DocumentPageLayout.objects.filter(version=version).delete()
        if items:
            DocumentPageLayout.objects.bulk_create(items)
    return len(items)
