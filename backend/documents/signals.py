"""Signale der Dokumente-App.

Automatik-Trigger: sobald ein Dokument den Trigger-Tag (Default „Psychologie")
erhält, werden asynchron MC-Lernkarten erzeugt und an **psychosr** gepusht
(siehe ``tasks.push_document_flashcards``).
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from .models import Document

logger = logging.getLogger(__name__)


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
