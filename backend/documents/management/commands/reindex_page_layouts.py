from django.core.management.base import BaseCommand

from documents import pipeline
from documents.models import DocumentVersion
from documents.services import page_layout


class Command(BaseCommand):
    """Backfill der wortgenauen OCR-Geometrie (Studio-Overlay) für den Altbestand.

    Die Layouts entstehen sonst nur während ``ocr_version()``; nach der Migration
    bliebe der bereits verarbeitete Bestand leer. Dieser Befehl liest die
    vorhandenen Archiv-PDFs OHNE erneute OCR (kein Neu-Siegeln gesiegelter
    READY-Versionen) und schreibt die Wortkästen nach – analog ``reindex_page_texts``.
    """

    help = "Erzeugt wortgenaue OCR-Layouts (Studio-Overlay) aus bestehenden Archiv-PDFs neu."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true", help="Bestehende Layouts ersetzen"
        )

    def handle(self, *args, **options):
        qs = DocumentVersion.objects.all().order_by("id")
        if not options["all"]:
            # Nur Versionen ohne Layout (idempotenter Nachlauf). ``distinct``, da der
            # Join über die Reverse-Relation sonst Dubletten liefern könnte.
            qs = qs.filter(page_layouts__isnull=True).distinct()

        indexed = 0
        skipped = 0
        cleared = 0
        for version in qs.iterator():
            # Zentrale Fallback-Kette Archiv -> Original (nicht blind
            # ``archive_path or file_path``): ist das Archiv gesetzt, aber
            # verschwunden, öffnet ``resolve_readable_version_path`` das vorhandene
            # Original. Für Nicht-PDFs/defekte Dateien liefert der Service leer.
            source = pipeline.resolve_readable_version_path(version) or version.file_path
            pages = page_layout.extract_page_layout(source)
            if not pages:
                # DATENSCHUTZ (P2): Eine leere Extraktion NICHT blind schreiben – sonst
                # löschte ``write_page_layout`` ein bestehendes Layout. Ist die Quelle
                # nur vorübergehend nicht erreichbar (z. B. NFS-Ausfall), wischte ein
                # ``--all``-Lauf sonst alle Studio-Daten weg. Wir überspringen und
                # lassen den Bestand unangetastet.
                skipped += 1
                continue
            count = page_layout.write_page_layout(version, pages)
            if count:
                indexed += 1
            else:
                cleared += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {indexed} Versionen mit Layout, {skipped} übersprungen "
                f"(kein/unerreichbares Layout, Bestand unangetastet), {cleared} geleert."
            )
        )
