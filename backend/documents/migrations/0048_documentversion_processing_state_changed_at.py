import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Additiv (PVC-sicher): Zeitstempel des letzten processing_state-Wechsels
    für den Stuck-Task-Watchdog. Bestandszeilen erhalten die Migrationszeit."""

    dependencies = [
        ("documents", "0047_documentchunk_dim_384"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentversion",
            name="processing_state_changed_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now, db_index=True
            ),
        ),
    ]
