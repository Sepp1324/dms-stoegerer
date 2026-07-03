import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0006_mailaccount_owner"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="retention_until",
            field=models.DateTimeField(
                blank=True,
                help_text="Löschsperre bis zu diesem Zeitpunkt (Aufbewahrungsfrist).",
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="RetentionPolicy",
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
                    "retention_months",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Aufbewahrungsfrist in Monaten (0 = keine Frist).",
                    ),
                ),
                (
                    "document_type",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="retention_policy",
                        to="documents.documenttype",
                    ),
                ),
            ],
            options={
                "verbose_name": "Aufbewahrungsfrist",
                "verbose_name_plural": "Aufbewahrungsfristen",
                "ordering": ["document_type__name"],
            },
        ),
    ]
