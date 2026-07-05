"""Backfill der Metadaten-Snapshots – Versionsvergleich Stufe 2 (STOAA-312).

Schreibt einen Metadaten-Snapshot **nur für die jeweils aktuelle Version** jedes
Dokuments aus dem *heutigen* Stand (``snapshot_taken_at = now``, als Erfassungs-
datum markiert). Ältere Versionen bleiben bewusst unberührt und damit 'nicht
verfügbar' – GoBD verbietet das Erfinden historischer Metadaten-Zustände, die nie
versiegelt wurden (Design-Entscheidung STOAA-292: ``backfill = nur aktuelle Version``).

    python manage.py backfill_version_snapshots [--dry-run]

Idempotent: eine Version mit vorhandenem Snapshot wird übersprungen (kein
Doppelschreiben). Der Snapshot wird – wie beim Sealing – kanonisch in den
``seal_hash`` der Version einbezogen.
"""
from django.core.management.base import BaseCommand

from documents.models import Document
from documents.services import version_snapshot


class Command(BaseCommand):
    help = (
        "Schreibt Metadaten-Snapshots für die jeweils aktuelle Version jedes "
        "Dokuments (idempotent, nur aktuelle Version)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur zählen, nichts schreiben.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        written = 0
        skipped = 0
        no_version = 0

        documents = Document.objects.select_related(
            "current_version", "document_type", "correspondent", "storage_path", "owner"
        )
        for document in documents.iterator():
            version = document.current_version
            if version is None:
                no_version += 1
                continue
            if version.metadata_snapshot is not None:
                skipped += 1
                continue
            if dry_run:
                written += 1
                continue
            if version_snapshot.write_snapshot_on_seal(version):
                written += 1
            else:  # Race: zwischenzeitlich doch schon geschrieben.
                skipped += 1

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Fertig: {written} Snapshots geschrieben, "
                f"{skipped} übersprungen (bereits vorhanden), "
                f"{no_version} ohne aktuelle Version."
            )
        )
