import os
import subprocess
import time
import uuid
from pathlib import Path

from celery.exceptions import SoftTimeLimitExceeded

from ._proc import run_group
from .types import OCRResult, OCRStatusEnum
from .extract import extract_text_best_effort
from .validate import is_valid_ocr


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _pdf_page_count(path: str) -> int | None:
    """Echte Seitenzahl via PyMuPDF; ``None`` wenn nicht lesbar/kein PDF."""
    try:
        import fitz

        with fitz.open(path) as doc:
            return doc.page_count or None
    except SoftTimeLimitExceeded:
        raise  # Soft-Time-Limit nie verschlucken
    except Exception:
        return None


def _pdf_page_texts(path: str) -> list[str]:
    """Text PRO SEITE via PyMuPDF (leere Liste, wenn nicht lesbar)."""
    try:
        import fitz

        with fitz.open(path) as doc:
            return [page.get_text() or "" for page in doc]
    except SoftTimeLimitExceeded:
        raise  # Soft-Time-Limit nie verschlucken
    except Exception:
        return []


def _estimate_pages(text: str) -> int:
    """Grobe Notfall-Schätzung, falls die echte Seitenzahl nicht lesbar ist."""
    return max(text.count("\n") // 50, 1)


def _should_skip_ocr(text: str, pages: int, *, force: bool) -> bool:
    """OCR überspringen, wenn bereits genug QUALITATIVER Text vorhanden ist.

    Nicht nur ``len(text) > 500``: ein 50-seitiges PDF mit 501 Zeichen gesamt hat
    zwar >500 Zeichen, aber nur ~10/Seite – das ist keine brauchbare Textschicht
    und würde ohne diese Prüfung fälschlich als SKIPPED (ohne OCR) durchgehen.
    Deshalb zusätzlich die Pro-Seite-Qualität via ``is_valid_ocr`` (mind. 20
    Zeichen/Seite). ``force`` überspringt nie.
    """
    if force:
        return False
    return len(text) > 500 and is_valid_ocr(text, pages)


def run_ocr(input_path: str, force: bool = False) -> OCRResult:
    """
    Stufe-2 OCR Engine

    FEATURES:
    - fallback extraction
    - validation
    - retry-ready
    - performance tracking
    """

    start = time.time()

    # 1. Erst versuchen: nur Text extrahieren
    text = extract_text_best_effort(input_path)

    # Original-Seitenzahl aus dem PDF (None bei Bild-Input). Dient (a) der
    # is_valid_ocr-Heuristik und (b) dem Vollständigkeits-Check: die OCR-Ausgabe
    # MUSS gleich viele Seiten haben, sonst ist sie unvollständig (z. B. 1 von 3).
    original_pages = _pdf_page_count(input_path)
    pages = original_pages or _estimate_pages(text)

    # 2. Skip OCR wenn bereits genug qualitativer Text vorhanden ist.
    if _should_skip_ocr(text, pages, force=force):
        return OCRResult(
            text=text,
            pages=pages,
            status=OCRStatusEnum.SKIPPED,
            duration_ms=int((time.time() - start) * 1000),
            engine="text-extraction",
        )

    # 3. OCRmyPDF Versuch. Ausgabe zuerst in eine EINDEUTIGE Temp-Datei; erst nach
    # Validierung atomar per os.replace zum endgültigen ``.ocr.pdf`` machen. Sonst
    # könnte ein teilweise geschriebenes (Timeout/Kill) oder ein von einem früheren
    # Versuch übrig gebliebenes .ocr.pdf als Archiv versiegelt werden.
    output_final = Path(input_path).with_suffix(".ocr.pdf")
    tmp = output_final.with_name(f"{output_final.stem}.{uuid.uuid4().hex}.tmp.pdf")
    try:
        # Hartes Prozess-Timeout + Prozessgruppen-Kill: ocrmypdf startet tesseract-
        # Kinder; ohne das könnten sie beim Celery-Hard-Limit weiterlaufen.
        run_group(
            [
                "ocrmypdf",
                "--force-ocr" if force else "--skip-text",
                input_path,
                str(tmp),
            ]
        )

        # Ausgabe VALIDIEREN, bevor sie veröffentlicht wird:
        #  * lesbares PDF (page_count != None),
        #  * KEINE fehlenden Seiten ggü. dem Original (Vollständigkeit),
        #  * genug Text pro Seite (is_valid_ocr).
        # Nur ein valides Ergebnis wird atomar zum Archiv. Ungültige/partielle
        # Ausgaben werden verworfen (kein Archiv) -> Vorschau/Siegel nutzen weiter
        # das Original; ocr_status=failed macht es im Monitoring/Retry sichtbar.
        page_texts = _pdf_page_texts(str(tmp))
        ocr_pages = len(page_texts) or _pdf_page_count(str(tmp))
        ocr_text = "\n".join(page_texts) if page_texts else extract_text_best_effort(str(tmp))
        pages_complete = original_pages is None or ocr_pages == original_pages

        # PRO-SEITE-Deckung statt nur Durchschnitt: sonst besteht ein 3-seitiges PDF,
        # bei dem NUR Seite 1 genug Text hat (Ø > 20 Zeichen/Seite), obwohl 2–3 leer
        # sind. Der Anteil der Seiten mit ausreichend Text muss eine Schwelle
        # erreichen; einzelne bewusst leere Seiten bleiben tolerierbar.
        from django.conf import settings as _settings

        min_chars = 20
        min_cov = float(getattr(_settings, "OCR_MIN_PAGE_COVERAGE", 0.6))
        if page_texts:
            with_text = sum(1 for t in page_texts if len(t.strip()) >= min_chars)
            coverage_ok = (with_text / len(page_texts)) >= min_cov
        else:
            coverage_ok = False

        valid = (
            ocr_pages is not None
            and pages_complete
            and coverage_ok
            and is_valid_ocr(ocr_text, ocr_pages)
        )

        if not valid:
            _remove_quietly(tmp)
            if not ocr_pages:
                reason = "kein lesbares PDF"
            elif not pages_complete:
                reason = f"unvollständig: {ocr_pages} statt {original_pages} Seiten"
            elif not coverage_ok:
                reason = "zu viele (fast) leere Seiten"
            else:
                reason = "zu wenig erkannter Text pro Seite"
            return OCRResult(
                text=ocr_text or text,
                pages=ocr_pages or pages,
                status=OCRStatusEnum.FAILED,
                error=f"OCR-Ausgabe verworfen ({reason})",
                duration_ms=int((time.time() - start) * 1000),
                engine="ocrmypdf",
                archive_path="",
            )

        os.replace(str(tmp), str(output_final))  # atomar: erst bei Erfolg Archiv

        return OCRResult(
            text=ocr_text,
            pages=ocr_pages,
            status=OCRStatusEnum.SUCCESS,
            duration_ms=int((time.time() - start) * 1000),
            engine="ocrmypdf",
            archive_path=str(output_final),
        )

    except SoftTimeLimitExceeded:
        _remove_quietly(tmp)
        raise  # Soft-Time-Limit propagieren (Task bricht ab), NICHT als FAILED tarnen
    except subprocess.TimeoutExpired:
        # OCR-Prozess-Timeout ist ein HARTER, retryfähiger Verarbeitungsfehler:
        # weiterwerfen -> _run_from markiert FAILED (Schritt "ocr"); Watchdog/Retry
        # greifen. NICHT als weicher OCRStatus.FAILED tarnen (sonst Pipeline -> READY).
        _remove_quietly(tmp)
        raise
    except Exception as e:
        _remove_quietly(tmp)
        return OCRResult(
            text=text,
            pages=pages,
            status=OCRStatusEnum.FAILED,
            error=str(e),
            duration_ms=int((time.time() - start) * 1000),
            engine="ocrmypdf",
            archive_path="",
        )