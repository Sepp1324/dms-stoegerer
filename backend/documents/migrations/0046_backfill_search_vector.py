"""Daten-Backfill des Suchvektors (P2 Teil 5b).

Läuft im migrate-initContainer VOR dem Start der neuen Backend-Pods, damit die
umgestellte (indexgestützte) Suche keinen Bestandsdokumente-Blindfleck hat –
unabhängig davon, ob der manuelle ``backfill_search_vectors``-Command lief.
Idempotent. Die Vektor-Logik ist bewusst inline (historisches Modell), damit die
Migration nicht von späteren Service-Änderungen abhängt.
"""
from __future__ import annotations

from django.contrib.postgres.search import SearchVector
from django.db import migrations
from django.db.models import Value

_CONFIG = "german"


def _forwards(apps, schema_editor):
    Document = apps.get_model("documents", "Document")
    qs = Document.objects.select_related(
        "correspondent", "document_type", "current_version"
    ).prefetch_related("tags")
    for document in qs:
        tag_names = " ".join(tag.name for tag in document.tags.all())
        correspondent = document.correspondent.name if document.correspondent_id else ""
        document_type = document.document_type.name if document.document_type_id else ""
        ocr_text = (
            document.current_version.ocr_text if document.current_version_id else ""
        )
        vector = (
            SearchVector(Value(document.title or ""), weight="A", config=_CONFIG)
            + SearchVector(Value(correspondent or ""), weight="A", config=_CONFIG)
            + SearchVector(Value(document_type or ""), weight="B", config=_CONFIG)
            + SearchVector(Value(tag_names), weight="B", config=_CONFIG)
            + SearchVector(Value(document.mail_subject or ""), weight="B", config=_CONFIG)
            + SearchVector(Value(document.mail_sender or ""), weight="B", config=_CONFIG)
            + SearchVector(Value(document.note or ""), weight="B", config=_CONFIG)
            + SearchVector(Value(ocr_text or ""), weight="D", config=_CONFIG)
        )
        Document.objects.filter(pk=document.pk).update(search_vector=vector)


def _noop(apps, schema_editor):
    # Rückwärts: nichts zu tun (die Spalte selbst rollt 0045 zurück).
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0045_document_search_vector"),
    ]

    operations = [
        migrations.RunPython(_forwards, _noop),
    ]
