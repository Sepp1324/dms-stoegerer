"""Signale der Dokumente-App.

Automatik-Trigger: sobald ein Dokument den Trigger-Tag (Default „Psychologie")
erhält, werden asynchron MC-Lernkarten erzeugt und an **psychosr** gepusht
(siehe ``tasks.push_document_flashcards``).
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from .models import Document

logger = logging.getLogger(__name__)


def _refresh_search_vector(document_id: int) -> None:
    """Suchvektor eines Dokuments neu schreiben – Fehler dürfen nie durchschlagen."""
    from .services.search_vector import update_search_vector_by_id

    try:
        update_search_vector_by_id(document_id)
    except Exception as exc:  # noqa: BLE001 – Vektor-Pflege darf save/Tagging nie brechen
        logger.warning("Suchvektor-Update fehlgeschlagen (doc %s): %s", document_id, exc)


# Felder, die in den Suchvektor einfließen (siehe services/search_vector.py).
# Ein gezielter ``save(update_fields=…)`` ohne eines dieser Felder muss den
# Vektor nicht neu schreiben – das spart Queries und vermeidet unnötige
# Schreiblast bei reinen Status-Updates der Pipeline.
_VECTOR_FIELDS = frozenset(
    {
        "title",
        "correspondent",
        "document_type",
        "mail_subject",
        "mail_sender",
        "note",
        "current_version",
    }
)


@receiver(post_save, sender=Document)
def on_document_saved_refresh_vector(
    sender, instance, created=False, update_fields=None, **kwargs
):
    """Hält den materialisierten Suchvektor bei relevanten Änderungen aktuell.

    Der Service nutzt ``.filter().update()`` → löst KEIN post_save aus, daher
    keine Rekursion. (Der OCR-Text kommt zusätzlich über den Pipeline-Hook, da
    dieser die Version, nicht zwingend das Dokument, speichert.)
    """
    if (
        not created
        and update_fields is not None
        and not (_VECTOR_FIELDS & set(update_fields))
    ):
        return
    _refresh_search_vector(instance.pk)


@receiver(m2m_changed, sender=Document.tags.through)
def on_document_tags_changed_refresh_vector(
    sender, instance, action, pk_set, reverse, model, **kwargs
):
    """Aktualisiert den Suchvektor, wenn sich die Tags eines Dokuments ändern."""
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if reverse:
        # instance = Tag; betroffene Dokumente stehen in pk_set (bei clear leer).
        for document_id in pk_set or ():
            _refresh_search_vector(document_id)
    else:
        _refresh_search_vector(instance.pk)


def _trigger_tag() -> str:
    return getattr(settings, "PSYCHOSR_TRIGGER_TAG", "Psychologie")


def _dispatch(document_id: int) -> None:
    from .tasks import push_document_flashcards

    try:
        push_document_flashcards.delay(document_id)
    except Exception as exc:  # noqa: BLE001 – Broker weg? Trigger darf das Taggen nie brechen
        logger.warning("psychosr-Trigger konnte nicht eingeplant werden: %s", exc)


@receiver(m2m_changed, sender=Document.tags.through)
def on_document_tags_changed(sender, instance, action, pk_set, reverse, model, **kwargs):
    """Feuert bei ``post_add`` von Tags. Nur der Trigger-Tag löst den Push aus.

    Forward (``document.tags.add(tag)``): ``instance`` = Document, ``pk_set`` =
    Tag-IDs. Reverse (``tag.documents.add(doc)``): ``instance`` = Tag,
    ``pk_set`` = Document-IDs.
    """
    if action != "post_add" or not pk_set:
        return

    if not getattr(settings, "PSYCHOSR_URL", "") or not getattr(settings, "PSYCHOSR_TOKEN", ""):
        return  # psychosr nicht konfiguriert -> gar nicht erst einplanen

    trigger = _trigger_tag()

    if reverse:
        # instance ist der Tag; nur reagieren, wenn es der Trigger-Tag ist.
        if getattr(instance, "name", None) != trigger:
            return
        for document_id in pk_set:
            _dispatch(document_id)
        return

    # Forward: instance ist das Document; prüfen, ob der Trigger-Tag dabei ist.
    if model.objects.filter(pk__in=pk_set, name=trigger).exists():
        _dispatch(instance.pk)
