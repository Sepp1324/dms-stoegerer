"""Migration: WORM-Felder + Aufbewahrungsfrist (STOAA-52)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0006_mailaccount_owner"),
    ]

    operations = [
        # DocumentType: Aufbewahrungsfrist in Monaten
        migrations.AddField(
            model_name="documenttype",
            name="retention_months",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Aufbewahrungsfrist in Monaten (0 = keine Frist)",
            ),
        ),
        # Document: berechnetes retention_until
        migrations.AddField(
            model_name="document",
            name="retention_until",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Löschen gesperrt bis zu diesem Datum (aus DocumentType.retention_months berechnet)",
            ),
        ),
        # DocumentVersion: retention_until
        migrations.AddField(
            model_name="documentversion",
            name="retention_until",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Löschen gesperrt bis zu diesem Datum",
            ),
        ),
    ]
