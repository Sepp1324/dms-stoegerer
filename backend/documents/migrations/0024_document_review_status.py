from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0023_backuprun_size"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="review_status",
            field=models.CharField(
                choices=[
                    ("needs_review", "Zu prüfen"),
                    ("reviewed", "Geprüft"),
                ],
                db_index=True,
                default="needs_review",
                help_text=(
                    "Fachlicher Inbox-Status: Metadaten/Einordnung geprüft oder offen."
                ),
                max_length=16,
            ),
        ),
    ]
