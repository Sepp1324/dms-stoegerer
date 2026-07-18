"""Pflege des materialisierten Volltext-Suchvektors (Perf, P2 Teil 5a).

Der Vektor spannt Felder aus mehreren Tabellen (Titel/Korrespondent/Dokumenttyp/
Tags/Mail/Notiz + OCR-Text der aktuellen Version). ``QuerySet.update()`` erlaubt
aber KEINE Join-Referenzen im SET-Ausdruck – deshalb werden die Texte in Python
eingesammelt und als ``Value()``-Literale in den ``SearchVector`` gesteckt
(join-frei, damit ``.update()`` funktioniert).

Gewichte identisch zur bisherigen Query-Zeit-Suche (STOAA-256): Titel/Korrespondent
= A, Dokumenttyp/Tags/Mail/Notiz = B, OCR-Fließtext = D.
"""
from __future__ import annotations

from django.contrib.postgres.search import SearchVector
from django.db.models import Value

from ..models import Document

_CONFIG = "german"


def build_search_vector(document: Document):
    """Baut den gewichteten ``SearchVector`` aus den Texten des Dokuments."""
    version = document.current_version
    tag_names = " ".join(tag.name for tag in document.tags.all())
    correspondent = (
        document.correspondent.name if document.correspondent_id else ""
    )
    document_type = (
        document.document_type.name if document.document_type_id else ""
    )
    ocr_text = version.ocr_text if version else ""

    return (
        SearchVector(Value(document.title or ""), weight="A", config=_CONFIG)
        + SearchVector(Value(correspondent or ""), weight="A", config=_CONFIG)
        + SearchVector(Value(document_type or ""), weight="B", config=_CONFIG)
        + SearchVector(Value(tag_names), weight="B", config=_CONFIG)
        + SearchVector(Value(document.mail_subject or ""), weight="B", config=_CONFIG)
        + SearchVector(Value(document.mail_sender or ""), weight="B", config=_CONFIG)
        + SearchVector(Value(document.note or ""), weight="B", config=_CONFIG)
        + SearchVector(Value(ocr_text or ""), weight="D", config=_CONFIG)
    )


def update_document_search_vector(document: Document) -> None:
    """Schreibt den Suchvektor eines Dokuments neu.

    Bewusst ``.filter(pk=...).update()``: löst KEIN ``post_save`` aus – der
    post_save-Handler, der diese Funktion aufruft, kann sich so nicht selbst
    rekursiv auslösen.
    """
    Document.objects.filter(pk=document.pk).update(
        search_vector=build_search_vector(document)
    )


def update_search_vector_by_id(document_id: int) -> None:
    """Wie ``update_document_search_vector``, nur über die ID (Pipeline-Hook)."""
    document = (
        Document.objects.filter(pk=document_id)
        .select_related("correspondent", "document_type", "current_version")
        .prefetch_related("tags")
        .first()
    )
    if document is not None:
        update_document_search_vector(document)
