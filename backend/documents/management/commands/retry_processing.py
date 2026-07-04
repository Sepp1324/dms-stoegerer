"""Verarbeitet fehlgeschlagene Versionen (processing_state=FAILED) erneut.

Der Retry setzt ``processing_state`` auf die Vorbedingung des fehlgeschlagenen
Schritts und läuft die Pipeline ab dort erneut (siehe pipeline.retry_version).

    python manage.py retry_processing --failed            # alle FAILED-Versionen
    python manage.py retry_processing --version-id <id>    # genau eine Version

Idempotent: Ein zweiter Lauf ohne neue Fehler verarbeitet 0 Versionen. WORM-/
READY-Versionen werden nie angefasst.
"""
from django.core.management.base import BaseCommand, CommandError

from documents import pipeline
from documents.models import DocumentVersion


class Command(BaseCommand):
    help = "Verarbeitet fehlgeschlagene Versionen (FAILED) erneut."

    def add_arguments(self, parser):
        parser.add_argument(
            "--failed",
            action="store_true",
            help="Alle Versionen mit processing_state=FAILED erneut verarbeiten.",
        )
        parser.add_argument(
            "--version-id",
            type=int,
            default=None,
            help="Genau eine Version (per ID) erneut verarbeiten.",
        )

    def handle(self, *args, **options):
        if not options["failed"] and options["version_id"] is None:
            raise CommandError(
                "Mindestens eines von --failed oder --version-id ist erforderlich."
            )

        qs = DocumentVersion.objects.select_related("document")
        if options["version_id"] is not None:
            qs = qs.filter(pk=options["version_id"])
        else:
            qs = qs.filter(processing_state=DocumentVersion.ProcessingState.FAILED)

        reprocessed = 0
        skipped = 0
        failed_again = 0

        for version in qs.iterator():
            # Nur echte FAILED-Versionen, die nicht gesiegelt/final sind.
            if version.processing_state != DocumentVersion.ProcessingState.FAILED or (
                version.is_immutable
                or version.processing_state
                in {
                    DocumentVersion.ProcessingState.SEALED,
                    DocumentVersion.ProcessingState.READY,
                }
            ):
                skipped += 1
                self.stdout.write(
                    f"  #{version.id} übersprungen "
                    f"(processing_state={version.processing_state}, "
                    f"immutable={version.is_immutable})"
                )
                continue

            result = pipeline.retry_version(version)
            if result.get("status") == "failed":
                failed_again += 1
                self.stderr.write(
                    f"  #{version.id} erneut fehlgeschlagen bei "
                    f"„{result.get('step')}“: {result.get('error')}"
                )
            else:
                reprocessed += 1
                self.stdout.write(
                    f"  #{version.id} „{version.document.title}“ – neu verarbeitet"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Fertig: {reprocessed} neu verarbeitet, "
                f"{skipped} übersprungen, {failed_again} erneut fehlgeschlagen."
            )
        )
