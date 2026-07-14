from django.db import migrations, models


class Migration(migrations.Migration):
    """Ordnerweite Familien-Freigabe: DocumentFolder.shared_with_household.

    Rein additiv (Default False) – gefahrlos auf bestehendem PVC.
    """

    dependencies = [
        ("documents", "0042_document_shared_with_household"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentfolder",
            name="shared_with_household",
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
