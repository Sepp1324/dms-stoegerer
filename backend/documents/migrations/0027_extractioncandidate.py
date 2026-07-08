from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0026_documentpagetext"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExtractionCandidate",
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
                    "field",
                    models.CharField(
                        choices=[
                            ("document_date", "Belegdatum"),
                            ("amount", "Betrag"),
                            ("iban", "IBAN"),
                            ("contract_number", "Vertragsnummer"),
                            ("policy_number", "Versicherungsnummer"),
                        ],
                        max_length=40,
                    ),
                ),
                ("value", models.CharField(max_length=512)),
                ("normalized_value", models.CharField(blank=True, max_length=512)),
                ("confidence", models.PositiveSmallIntegerField(default=50)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("source", models.CharField(default="heuristic", max_length=32)),
                ("source_page", models.PositiveIntegerField(blank=True, null=True)),
                ("source_snippet", models.TextField(blank=True)),
                ("source_snippet_html", models.TextField(blank=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Offen"),
                            ("applied", "Übernommen"),
                            ("dismissed", "Verworfen"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("dismissed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="extraction_candidates",
                        to="documents.document",
                    ),
                ),
            ],
            options={
                "verbose_name": "Extraktionsvorschlag",
                "verbose_name_plural": "Extraktionsvorschläge",
                "ordering": ["document_id", "field", "-confidence", "source_page"],
            },
        ),
        migrations.AddIndex(
            model_name="extractioncandidate",
            index=models.Index(
                fields=["document", "status"],
                name="documents_e_documen_9deba9_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="extractioncandidate",
            index=models.Index(
                fields=["field", "status"],
                name="documents_e_field_e2f4ab_idx",
            ),
        ),
    ]
