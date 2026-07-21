import time
import subprocess
from pathlib import Path

from .types import OCRResult, OCRStatusEnum
from .extract import extract_text_best_effort
from .validate import is_valid_ocr


def _pdf_page_count(path: str) -> int | None:
    """Echte Seitenzahl via PyMuPDF; ``None`` wenn nicht lesbar/kein PDF."""
    try:
        import fitz

        with fitz.open(path) as doc:
            return doc.page_count or None
    except Exception:
        return None


def _estimate_pages(text: str) -> int:
    """Grobe Notfall-Schätzung, falls die echte Seitenzahl nicht lesbar ist."""
    return max(text.count("\n") // 50, 1)


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

    # Seitenzahl aus dem PDF selbst; die Zeilenumbruch-Schätzung ist nur Fallback.
    # Wichtig für is_valid_ocr unten: eine zu KLEINE Seitenzahl täuscht „genug Text
    # pro Seite" vor und ließe lückenhafte OCR (nur 1 von 3 Seiten erkannt)
    # fälschlich als SUCCESS gelten.
    pages = _pdf_page_count(input_path) or _estimate_pages(text)

    # 2. Skip OCR wenn gut genug
    if not force and len(text) > 500:
        return OCRResult(
            text=text,
            pages=pages,
            status=OCRStatusEnum.SKIPPED,
            duration_ms=int((time.time() - start) * 1000),
            engine="text-extraction",
        )

    # 3. OCRmyPDF Versuch
    try:
        output = Path(input_path).with_suffix(".ocr.pdf")

        subprocess.run(
            [
                "ocrmypdf",
                "--force-ocr" if force else "--skip-text",
                input_path,
                str(output),
            ],
            check=True,
        )

        text = extract_text_best_effort(str(output))
        # Seitenzahl aus dem erzeugten PDF (autoritativ) für die Qualitätsheuristik.
        pages = _pdf_page_count(str(output)) or pages

        valid = is_valid_ocr(text, pages)

        return OCRResult(
            text=text,
            pages=pages,
            status=OCRStatusEnum.SUCCESS if valid else OCRStatusEnum.FAILED,
            duration_ms=int((time.time() - start) * 1000),
            engine="ocrmypdf",
        )

    except Exception as e:
        return OCRResult(
            text=text,
            pages=pages,
            status=OCRStatusEnum.FAILED,
            error=str(e),
            duration_ms=int((time.time() - start) * 1000),
            engine="ocrmypdf",
        )