def is_valid_ocr(text: str, pages: int) -> bool:
    """
    OCR Quality Heuristic

    WHY:
    - erkennt "leere OCRs"
    - verhindert falsche SUCCESS states
    """

    if not text:
        return False

    chars_per_page = len(text) / max(pages, 1)

    # heuristische Schwelle
    if chars_per_page < 20:
        return False

    return True