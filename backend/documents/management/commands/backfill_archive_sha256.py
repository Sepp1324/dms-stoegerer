"""Berechnet ``archive_sha256`` für Alt-Versionen ohne hinterlegten Archivhash.

Migration 0061 ließ ``archive_sha256`` bei allen Bestandsversionen bewusst leer
(kein Backfill in der Migration – teures Lesen ganzer Dateien gehört nicht in eine
Schema-Migration). Ohne Hash gilt ein vorhandenes Archiv in der Live-Prüfung nur als
existent, aber nicht als inhaltlich geprüft. Dieser explizite, kontrollierte Command
holt den Hash für vertrauenswürdig geprüfte Bestände nach.

    python manage.py backfill_archive_sha256            # setzt fehlende Hashes
    python manage.py backfill_archive_sha256 --dry-run  # nur zählen, nichts schreiben

WORM-sicher: schreibt ausschließlich das operative Feld ``archive_sha256`` per
``QuerySet.update()`` (umgeht den save()-Immutable-Guard); Inhalt/Siegel bleiben
unberührt.
"""
import os

from django.core.management.base import BaseCommand

from documents import pipeline
from documents.models import DocumentVersion


class Command(BaseCommand):
    help = "Berechnet fehlende archive_sha256-Werte für bestehende Archiv-PDFs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur zählen/anzeigen, nichts schreiben.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        qs = DocumentVersion.objects.filter(archive_sha256="").exclude(
            archive_path=""
        ).order_by("id")

        updated = 0
        missing = 0
        unreadable = 0
        for version in qs.iterator():
            path = version.archive_path
            if not path or not os.path.exists(path):
                missing += 1
                continue
            try:
                digest = pipeline.sha256_of(path)
            except OSError:
                # Datei/Mount/Rechte -> überspringen (nicht abbrechen), spätere Läufe
                # holen es nach.
                unreadable += 1
                continue
            if not dry_run:
                # WORM-sicher: nur das operative Feld setzen (kein save()-Guard).
                DocumentVersion.objects.filter(pk=version.pk).update(
                    archive_sha256=digest
                )
            updated += 1

        verb = "würde setzen" if dry_run else "gesetzt"
        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {updated} Hashes {verb}, {missing} Archiv fehlt, "
                f"{unreadable} nicht lesbar."
            )
        )
