from django.db import migrations, models


class Migration(migrations.Migration):
    """Additive: persistierter psychosr-Kartenspeicher pro Version (Teil-Retry-Idempotenz).

    Reines Feld-Add mit default=list – kein Datenrewrite, PVC-/WORM-neutral.
    """

    dependencies = [
        ("documents", "0049_documentversion_seal_finalized_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentversion",
            name="flashcards_sync",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Persistierte MC-Lernkarten + Pro-Karte-Push-Status (psychosr)",
            ),
        ),
    ]
