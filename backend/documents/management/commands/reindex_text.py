"""Zieht den Volltext bestehender Dokumente neu aus den Archiv-PDFs.

Nötig für Dokumente, die vor der pdftotext-Umstellung verarbeitet wurden und
deren ``ocr_text`` (z. B. bei digitalen PDFs) leer geblieben ist.

    python manage.py reindex_text          # nur Versionen ohne Text
    python manage.py reindex_text --all    # alle Versionen neu einlesen
"""
import os

from django.core.management.base import BaseCommand

from documents import pipeline
from documents.models import DocumentVersion


class Command(BaseCommand):
    help = "Extrahiert den Volltext bestehender Dokumente neu (pdftotext)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Auch Versionen neu einlesen, die bereits Text haben.",
        )

    def handle(self, *args, **options):
        qs = DocumentVersion.objects.all()
        if not options["all"]:
            qs = qs.filter(ocr_text="")

        updated = 0
        skipped = 0
        for version in qs.iterator():
            src = version.archive_path or version.file_path
            if not src or not os.path.exists(src):
                skipped += 1
                continue
            text = pipeline.extract_text(src)
            if text.strip():
                version.ocr_text = text
                version.save(update_fields=["ocr_text"])
                updated += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(f"Fertig: {updated} aktualisiert, {skipped} übersprungen.")
        )
