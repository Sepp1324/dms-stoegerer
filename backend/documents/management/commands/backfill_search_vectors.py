"""Backfill des materialisierten Suchvektors (search_vector) für alle Dokumente.

Nach dem Deploy von Teil 5a EINMAL laufen lassen, damit Bestandsdokumente den
Vektor gefüllt bekommen (neue/aktualisierte pflegen ihn ab dann automatisch über
Signale + Pipeline-Hook). Idempotent – kann gefahrlos erneut laufen.

    python manage.py backfill_search_vectors [--batch-size 200]
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from documents.models import Document
from documents.services.search_vector import update_document_search_vector


class Command(BaseCommand):
    help = "Füllt den Suchvektor (search_vector) für alle vorhandenen Dokumente."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args, **options):
        batch = options["batch_size"]
        qs = (
            Document.objects.select_related(
                "correspondent", "document_type", "current_version"
            )
            .prefetch_related("tags")
            .order_by("pk")
        )
        total = qs.count()
        done = 0
        for document in qs.iterator(chunk_size=batch):
            update_document_search_vector(document)
            done += 1
            if done % batch == 0:
                self.stdout.write(f"  {done}/{total} …")
        self.stdout.write(
            self.style.SUCCESS(f"Fertig: {done}/{total} Dokumente aktualisiert.")
        )
