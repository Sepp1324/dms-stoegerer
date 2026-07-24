from django.core.management.base import BaseCommand

from documents import pipeline
from documents.models import DocumentVersion
from documents.services import page_text


class Command(BaseCommand):
    help = "Erzeugt seitengenaue OCR-Texte für Copilot-Quellen neu."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Bestehende Seitentexte ersetzen")

    def handle(self, *args, **options):
        qs = DocumentVersion.objects.all().order_by("id")
        if not options["all"]:
            qs = qs.filter(page_texts__isnull=True).distinct()

        indexed = 0
        skipped = 0
        for version in qs.iterator():
            # Zentrale Fallback-Kette Archiv -> Original (nicht blind
            # ``archive_path or file_path``): ist das Archiv gesetzt, aber
            # verschwunden, wird sonst NICHT das vorhandene Original geöffnet,
            # sondern der ganze OCR-Text als eine einzige Seite gespeichert – dann
            # springen seitengenaue Copilot-Quellen auf falsche Seiten.
            source = pipeline.resolve_readable_version_path(version) or version.file_path
            pages = page_text.extract_page_texts(source, fallback_text=version.ocr_text)
            count = page_text.write_page_texts(version, pages)
            if count:
                indexed += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(f"Fertig: {indexed} Versionen indexiert, {skipped} übersprungen.")
        )
