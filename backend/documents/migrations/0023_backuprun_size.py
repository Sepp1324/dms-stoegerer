from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0022_backupmonitor"),
    ]

    operations = [
        migrations.AddField(
            model_name="backupmonitor",
            name="size_bytes",
            field=models.BigIntegerField(
                blank=True,
                help_text="Größe des letzten Backup-Artefakts in Bytes.",
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="BackupRun",
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
                        max_length=16,
                    ),
                ),
                (
                    "artifact_timestamp",
                    models.CharField(blank=True, default="", max_length=32),
                ),
                ("size_bytes", models.BigIntegerField(blank=True, null=True)),
                ("message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Backup-Lauf",
                "verbose_name_plural": "Backup-Läufe",
                "ordering": ["-created_at"],
            },
        ),
    ]
