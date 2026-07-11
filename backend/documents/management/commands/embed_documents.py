"""Backfill: erzeugt Embeddings für den Bestand (semantische Suche).

    python manage.py embed_documents            # aktuelle Version je Dokument, via Queue
    python manage.py embed_documents --all       # alle Versionen mit OCR-Text
    python manage.py embed_documents --sync      # inline statt Celery-Queue
"""
from django.core.management.base import BaseCommand

from documents.models import Document, DocumentVersion
from documents.tasks import embed_document_version


class Command(BaseCommand):
    help = "Erzeugt Embeddings für vorhandene Dokumente (semantische Suche)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Alle Versionen mit OCR-Text (sonst nur die current_version je Dokument).",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Inline berechnen statt in die Celery-Queue einreihen.",
        )

    def handle(self, *args, **options):
        if options["all"]:
            ids = list(
                DocumentVersion.objects.exclude(ocr_text="")
                .values_list("id", flat=True)
            )
        else:
            ids = list(
                Document.objects.filter(current_version__isnull=False)
                .exclude(current_version__ocr_text="")
                .values_list("current_version_id", flat=True)
            )

        for vid in ids:
            if options["sync"]:
                embed_document_version(vid)
            else:
                embed_document_version.delay(vid)

        mode = "inline berechnet" if options["sync"] else "in die Queue eingereiht"
        self.stdout.write(
            self.style.SUCCESS(f"{len(ids)} Version(en) {mode}.")
        )
