from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0021_ingest_source_mobile_help_text"),
    ]

    operations = [
        migrations.CreateModel(
            name="BackupMonitor",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("backup", "Backup"),
                            ("restore_drill", "Restore-Drill"),
                        ],
                        max_length=32,
                        unique=True,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("unknown", "Unbekannt"),
                            ("running", "Läuft"),
                            ("success", "Erfolgreich"),
                            ("failed", "Fehlgeschlagen"),
                        ],
                        default="unknown",
                        max_length=16,
                    ),
                ),
                (
                    "artifact_timestamp",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Backup-Zeitstempel wie 20260706-084501.",
                        max_length=32,
                    ),
                ),
                ("message", models.TextField(blank=True, default="")),
                ("last_started_at", models.DateTimeField(blank=True, null=True)),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("last_finished_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Backup-Monitor",
                "verbose_name_plural": "Backup-Monitoring",
                "ordering": ["kind"],
            },
        ),
    ]
