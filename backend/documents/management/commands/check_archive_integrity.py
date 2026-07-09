"""Archiv-/Integritätsprüfung für Dokumente.

Beispiele:

    python manage.py check_archive_integrity --all
    python manage.py check_archive_integrity --status unchecked --limit 100
    python manage.py check_archive_integrity --document-id 42
"""
from django.core.management.base import BaseCommand, CommandError

from documents.models import Document
from documents.services import archive


class Command(BaseCommand):
    help = "Prüft Datei-Hash-Kette, Metadaten-Siegel und Archivstatus."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Alle Dokumente prüfen.")
        parser.add_argument("--document-id", type=int, help="Nur ein Dokument prüfen.")
        parser.add_argument(
            "--status",
            choices=[choice for choice, _label in Document.ArchiveStatus.choices],
            default=Document.ArchiveStatus.UNCHECKED,
            help="Status-Filter, wenn nicht --all oder --document-id verwendet wird.",
        )
        parser.add_argument("--limit", type=int, default=100, help="Maximale Anzahl.")

    def handle(self, *args, **options):
        limit = max(1, int(options["limit"]))
        qs = Document.objects.select_related("current_version").prefetch_related("versions")

        if options["document_id"]:
            qs = qs.filter(pk=options["document_id"])
            if not qs.exists():
                raise CommandError(f"Dokument {options['document_id']} nicht gefunden.")
        elif options["all"]:
            qs = qs.order_by("id")
        else:
            qs = qs.filter(archive_status=options["status"]).order_by("id")

        checked = 0
        ok = warning = error = 0
        for document in qs[:limit]:
            report = archive.verify_document_archive(document)
            checked += 1
            if report["status"] == Document.ArchiveStatus.OK:
                ok += 1
            elif report["status"] == Document.ArchiveStatus.WARNING:
                warning += 1
            else:
                error += 1
            self.stdout.write(
                f"{document.id}: {report['status']} "
                f"({len(report['errors'])} Fehler, {len(report['warnings'])} Warnungen)"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {checked} geprüft, {ok} ok, {warning} Warnung, {error} Fehler."
            )
        )
