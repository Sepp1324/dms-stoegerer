from django.db import migrations, models


class Migration(migrations.Migration):
    """Additiv (PVC-sicher): verlässlicher Siegel-Abschlussmarker. Bestandszeilen
    erhalten NULL – für bereits gesiegelte Altversionen ist das unkritisch, weil
    finalize_sealed_version nur SEALED-Versionen anfasst (READY sind längst durch)."""

    dependencies = [
        ("documents", "0048_documentversion_processing_state_changed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentversion",
            name="seal_finalized_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
