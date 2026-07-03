"""Verarbeitet Dokumente neu: OCR-Pipeline + regelbasierte Klassifizierung.

Nötig für Dokumente, deren OCR früher fehlgeschlagen ist (z. B. wegen der
pikepdf-Inkompatibilität). Standardmäßig nur Versionen ohne Text.

    python manage.py reprocess          # nur Dokumente ohne OCR-Text
    python manage.py reprocess --all    # alle Dokumente neu verarbeiten

Für reine Text-Nachindizierung (ohne OCR) siehe ``reindex_text``; für nur die
Regeln siehe ``reclassify``.
"""
from django.core.management.base import BaseCommand

from documents import classification, pipeline
from documents.models import DocumentVersion


class Command(BaseCommand):
    help = "Verarbeitet Dokumente neu (OCR + Klassifizierung)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Alle Versionen neu verarbeiten, nicht nur solche ohne Text.",
        )

    def handle(self, *args, **options):
        qs = DocumentVersion.objects.select_related("document")
        if not options["all"]:
            qs = qs.filter(ocr_text="")

        done = 0
        failed = 0
        for version in qs.iterator():
            try:
                pipeline.process_version(version)
                classification.apply_rules(version.document)
                done += 1
                self.stdout.write(
                    f"  #{version.document_id} „{version.document.title}“ "
                    f"– {len(version.ocr_text)} Zeichen"
                )
            except Exception as exc:  # pragma: no cover - abhängig vom PDF
                failed += 1
                self.stderr.write(f"  #{version.document_id} FEHLER: {exc}")

        self.stdout.write(
            self.style.SUCCESS(f"Fertig: {done} verarbeitet, {failed} fehlgeschlagen.")
        )
