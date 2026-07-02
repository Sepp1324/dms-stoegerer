"""Celery-Tasks für KI-gestützte Verarbeitung (asynchron, außerhalb des Requests)."""
from celery import shared_task

from .services import suggest_metadata


@shared_task
def suggest_metadata_task(ocr_text: str) -> dict:
    """Erzeugt KI-Metadatenvorschläge im Hintergrund.

    Wird in Stufe 2 an die Pipeline gehängt und schreibt Vorschläge an das
    Dokument (zum Bestätigen durch den Nutzer), statt sie direkt zu setzen.
    """
    return suggest_metadata(ocr_text)
