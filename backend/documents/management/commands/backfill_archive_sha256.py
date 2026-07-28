"""Berechnet ``archive_sha256`` für Alt-Versionen ohne hinterlegten Archivhash.

Migration 0061 ließ ``archive_sha256`` bei allen Bestandsversionen bewusst leer
(kein Backfill in der Migration – teures Lesen ganzer Dateien gehört nicht in eine
Schema-Migration). Ohne Hash gilt ein vorhandenes Archiv in der Live-Prüfung nur als
existent, aber nicht als inhaltlich geprüft. Dieser explizite, kontrollierte Command
holt den Hash für vertrauenswürdig geprüfte Bestände nach.

    python manage.py backfill_archive_sha256 --dry-run  # nur zählen, nichts schreiben
    python manage.py backfill_archive_sha256 --yes      # tatsächlich schreiben

VERTRAUENS-Warnung (P2): Der Command hasht die AKTUELL auf dem Volume liegende
Archivdatei und schreibt den Wert als WORM-Baseline. Eine bereits manipulierte
Datei bestünde danach alle künftigen Hashprüfungen. Deshalb:

  * Schreiben erfordert ausdrücklich ``--yes`` (eine bewusste Vertrauens-Bestätigung,
    dass der Bestand als integer gilt).
  * Jeder gesetzte Hash wird als ``AuditLogEntry`` (action=``archive_sha256_backfill``)
    protokolliert – mit Version, Dokument, Pfad und Digest, damit nachvollziehbar
    bleibt, welche Datei zu welchem Zeitpunkt zur Basis erklärt wurde.
  * Es werden NUR leere ``archive_sha256`` gefüllt, nie ein vorhandener Hash
    überschrieben (kein stilles Neu-Basieren einer bereits geprüften Version).

WORM-sicher: schreibt ausschließlich das operative Feld ``archive_sha256`` per
``QuerySet.update()`` (umgeht den save()-Immutable-Guard); Inhalt/Siegel bleiben
unberührt.
"""
import os

from django.core.management.base import BaseCommand
from django.db import transaction

from documents import pipeline
from documents.models import AuditLogEntry, DocumentVersion


class Command(BaseCommand):
    help = "Berechnet fehlende archive_sha256-Werte für bestehende Archiv-PDFs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur zählen/anzeigen, nichts schreiben.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Vertrauens-Bestätigung – ohne diese Flag (und ohne --dry-run) "
            "wird nichts geschrieben.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        confirmed = options["yes"]

        if not dry_run and not confirmed:
            self.stderr.write(
                "Abbruch: --yes erforderlich. Der Backfill erklärt die aktuell auf "
                "dem Volume liegenden Archivdateien zur unveränderlichen Hash-Basis; "
                "das ist nur bei vertrauenswürdigem Bestand zulässig. (Zum reinen "
                "Zählen: --dry-run.)"
            )
            return

        # Nur LEERE Hashes füllen – ein vorhandener archive_sha256 wird NIE
        # überschrieben (kein stilles Neu-Basieren einer bereits geprüften Version).
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
                # Setzen + Audit atomar, wieder als CAS gegen leeren Hash (falls ein
                # paralleler Lauf denselben zuerst füllt -> kein doppeltes Audit).
                with transaction.atomic():
                    written = DocumentVersion.objects.filter(
                        pk=version.pk, archive_sha256=""
                    ).update(archive_sha256=digest)
                    if not written:
                        continue
                    AuditLogEntry.objects.create(
                        action="archive_sha256_backfill",
                        object_type="DocumentVersion",
                        object_id=str(version.pk),
                        detail={
                            "document_id": version.document_id,
                            "archive_path": path,
                            "archive_sha256": digest,
                        },
                    )
            updated += 1

        verb = "würde setzen" if dry_run else "gesetzt"
        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {updated} Hashes {verb}, {missing} Archiv fehlt, "
                f"{unreadable} nicht lesbar."
            )
        )
