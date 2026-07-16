from django.db import migrations, models


class Migration(migrations.Migration):
    """Freie Notiz je Dokument (rein additiv, Default '')."""

    dependencies = [
        ("documents", "0043_folder_shared_with_household"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="note",
            field=models.TextField(blank=True, default=""),
        ),
    ]
