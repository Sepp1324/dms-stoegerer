from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from documents.models import BackupMonitor


class Command(BaseCommand):
    help = "Schreibt Backup-/Restore-Drill-Status für UI/Admin-Monitoring."

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind",
            choices=[BackupMonitor.Kind.BACKUP, BackupMonitor.Kind.RESTORE_DRILL],
            required=True,
        )
        parser.add_argument(
            "--status",
            choices=[
                BackupMonitor.Status.RUNNING,
                BackupMonitor.Status.SUCCESS,
                BackupMonitor.Status.FAILED,
                BackupMonitor.Status.UNKNOWN,
            ],
            required=True,
        )
        parser.add_argument("--artifact-timestamp", default="")
        parser.add_argument("--message", default="")

    def handle(self, *args, **options):
        kind = options["kind"]
        status = options["status"]
        now = timezone.now()

        obj, _created = BackupMonitor.objects.get_or_create(
            kind=kind,
            defaults={"status": BackupMonitor.Status.UNKNOWN},
        )

        obj.status = status
        obj.artifact_timestamp = options["artifact_timestamp"] or obj.artifact_timestamp
        obj.message = options["message"]

        if status == BackupMonitor.Status.RUNNING:
            obj.last_started_at = now
        elif status == BackupMonitor.Status.SUCCESS:
            obj.last_success_at = now
            obj.last_finished_at = now
        elif status == BackupMonitor.Status.FAILED:
            obj.last_finished_at = now
        elif status == BackupMonitor.Status.UNKNOWN:
            obj.last_finished_at = now
        else:  # pragma: no cover - argparse choices verhindern das.
            raise CommandError(f"Unbekannter Status: {status}")

        obj.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"{obj.kind}: {obj.status} ({obj.artifact_timestamp or 'ohne TS'})"
            )
        )
