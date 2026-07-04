import subprocess
import fitz  # PyMuPDF


def extract_text_poppler(path: str) -> str:
    """Standard PDF text extraction (schnell)"""
    try:
        return subprocess.check_output(["pdftotext", path, "-"]).decode()
    except Exception:
        return ""


def extract_text_pymupdf(path: str) -> str:
    """Fallback extraction (robuster bei kaputten PDFs)"""
    try:
        doc = fitz.open(path)
        return "\n".join(page.get_text() for page in doc)
    except Exception:
        return ""


def extract_text_best_effort(path: str) -> str:
    """
    Multi-layer extraction
    WHY:
    - PDFs sind oft kaputt oder unterschiedlich generiert
    """
    text = extract_text_poppler(path)

    if len(text.strip()) > 100:
        return text

    text = extract_text_pymupdf(path)

    return text