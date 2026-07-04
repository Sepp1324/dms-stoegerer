from dataclasses import dataclass
from enum import Enum


class OCRStatusEnum(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class OCRResult:
    """
    Zentrale Rückgabe der OCR-Pipeline
    WHY:
    - entkoppelt Pipeline von Django Models
    - testbar ohne DB
    - Celery-safe
    """

    text: str
    pages: int
    status: OCRStatusEnum
    error: str | None = None
    duration_ms: int = 0
    engine: str = "ocrmypdf"