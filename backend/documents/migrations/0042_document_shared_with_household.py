from django.db import migrations, models


class Migration(migrations.Migration):
    """Familien-Freigabe: shared_with_household-Flag (rein additiv, Default False)."""

    dependencies = [
        ("documents", "0041_document_superseded_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="shared_with_household",
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
