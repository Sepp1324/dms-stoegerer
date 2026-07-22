from django.db import migrations, models


class Migration(migrations.Migration):
    """Additiv: last_error (Monitoring) + neue Choice ``failed`` für
    FlashcardSyncEntry.state (Choices sind DB-seitig nur CharField -> nur das
    Feld last_error ist eine echte Schemaänderung).
    """

    dependencies = [
        ("documents", "0051_flashcard_sync_entry"),
    ]

    operations = [
        migrations.AddField(
            model_name="flashcardsyncentry",
            name="last_error",
            field=models.TextField(
                blank=True, default="", help_text="Letzter Push-Fehler (Monitoring)"
            ),
        ),
        migrations.AlterField(
            model_name="flashcardsyncentry",
            name="state",
            field=models.CharField(
                choices=[
                    ("pending", "Ausstehend"),
                    ("in_progress", "Wird gesendet"),
                    ("pushed", "Gesendet"),
                    ("failed", "Endgültig fehlgeschlagen"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
    ]
