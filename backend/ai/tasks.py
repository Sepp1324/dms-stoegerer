"""Celery-Tasks für KI-gestützte Verarbeitung (asynchron, außerhalb des Requests)."""
from celery import shared_task
from django.utils import timezone

from .services import suggest_metadata


@shared_task
def suggest_metadata_task(ocr_text: str) -> dict:
    """Erzeugt KI-Metadatenvorschläge aus reinem OCR-Text (ohne Speicherung)."""
    return suggest_metadata(ocr_text)


@shared_task
def suggest_document_metadata(document_id: int) -> dict:
    """Erzeugt Vorschläge für ein Dokument und speichert sie an ``ai_suggestions``.

    Wird nach der OCR-Pipeline angestoßen. Die Vorschläge sind unverbindlich –
    der Nutzer bestätigt sie in der Detailansicht (apply_suggestions).
    """
    # Import lazy, um Import-Reihenfolge/Zyklen zu vermeiden.
    from documents.models import Document

    try:
        document = Document.objects.select_related("current_version").get(pk=document_id)
    except Document.DoesNotExist:
        return {"status": "missing", "document_id": document_id}

    version = document.current_version
    text = (version.ocr_text if version else "") or ""
    if not text.strip():
        return {"status": "no_text", "document_id": document_id}

    result = suggest_metadata(text)
    suggestions = result.get("suggestions") or {}
    if result.get("source") != "ai" or not suggestions:
        return {"status": result.get("source", "unavailable"), "document_id": document_id}

    # Bereits hinterlegte Vorschläge (z. B. Absender→Correspondent aus der
    # E-Mail-Ingestion) erhalten; die KI hat bei Überschneidung Vorrang.
    merged = {**(document.ai_suggestions or {}), **suggestions}
    document.ai_suggestions = merged
    document.ai_suggested_at = timezone.now()
    document.save(update_fields=["ai_suggestions", "ai_suggested_at"])

    from documents.services import review_tasks

    review_tasks.sync_document_review_tasks(document)
    return {"status": "done", "document_id": document_id, "suggestions": merged}
