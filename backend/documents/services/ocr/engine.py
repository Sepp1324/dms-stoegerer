import time
import subprocess
from pathlib import Path

from .types import OCRResult, OCRStatusEnum
from .extract import extract_text_best_effort
from .validate import is_valid_ocr


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

    # Dummy page estimation
    pages = max(text.count("\n") // 50, 1)

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