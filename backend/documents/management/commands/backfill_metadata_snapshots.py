"""Backfill der Metadaten-Snapshots für Altbestand (STOAA-315, current-only).

Eigentümer-Vorgabe (verbindlich): Beim Deploy erhält **nur** die *aktuelle*
Version (``Document.current_version``) jedes Dokuments einen Snapshot – Label
„Stand Erfassungsdatum". Ältere Vor-Feature-Versionen bleiben ``null`` →
API/UX „nicht verfügbar" (identisch zur Stufe-1-UX). **Kein** Voll-Backfill
historischer Zustände.

Deploy-Schritt (nach der Migration 0018 EINMALIG ausführen):

    python manage.py backfill_metadata_snapshots

Idempotent: Eine Version, die bereits einen ``metadata_snapshot`` trägt, wird
übersprungen – ein zweiter Lauf ändert 0 Datensätze. Geschrieben wird per
``QuerySet.update`` (umgeht den WORM-``save``-Guard, wie ``transition_to``),
damit auch bereits gesiegelte current_versions befüllt werden können. Je
befüllter Version entsteht ein Audit-Eintrag ``action="metadata_backfill"``.
"""
from django.core.management.base import BaseCommand

from documents.models import AuditLogEntry, Document, DocumentVersion
from documents.services.metadata_snapshot import (
    build_metadata_snapshot,
    compute_seal_hash,
)


class Command(BaseCommand):
    help = (
        "Backfillt Metadaten-Snapshots NUR auf der current_version jedes "
        "Dokuments (idempotent, current-only)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur zählen, was befüllt würde – nichts schreiben.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        created = 0
        skipped = 0
        no_version = 0

        documents = Document.objects.select_related(
            "current_version", "correspondent", "document_type", "storage_path", "owner"
        ).iterator()

        for document in documents:
            version = document.current_version
            if version is None:
                no_version += 1
                continue
            # Idempotenz: bereits befüllte Version nicht anfassen (Re-Run-sicher).
            if version.metadata_snapshot is not None:
                skipped += 1
                continue

            snapshot = build_metadata_snapshot(document)
            seal_hash = compute_seal_hash(
                version.sha256, version.prev_hash, snapshot
            )

            if dry_run:
                created += 1
                continue

            # WORM-Guard umgehen (wie transition_to / _seal_version): QuerySet.update.
            DocumentVersion.objects.filter(pk=version.pk).update(
                metadata_snapshot=snapshot,
                seal_hash=seal_hash,
            )
            AuditLogEntry.objects.create(
                actor=document.owner,
                action="metadata_backfill",
                object_type="DocumentVersion",
                object_id=str(version.id),
                detail={
                    "version_no": version.version_no,
                    "label": "Stand Erfassungsdatum",
                    "current_only": True,
                },
            )
            created += 1

        verb = "würde befüllen" if dry_run else "befüllt"
        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {created} current_version(s) {verb}, {skipped} bereits "
                f"vorhanden (übersprungen), {no_version} ohne current_version."
            )
        )
