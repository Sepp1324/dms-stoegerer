from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0028_casefile_document_case_file"),
    ]

    operations = [
        migrations.CreateModel(
            name="CaseFileCandidate",
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
                    "kind",
                    models.CharField(
                        choices=[
                            ("existing_case", "Bestehende Akte"),
                            ("new_case", "Neue Akte"),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    "suggested_title",
                    models.CharField(
                        blank=True,
                        help_text="Titelvorschlag, wenn kind=new_case ist.",
                        max_length=255,
                    ),
                ),
                (
                    "signature",
                    models.CharField(
                        help_text=(
                            "Idempotenz-Schlüssel pro Dokument; verhindert "
                            "wiederkehrende Duplikate."
                        ),
                        max_length=128,
                    ),
                ),
                ("score", models.PositiveSmallIntegerField(default=50)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("signals", models.JSONField(blank=True, default=list)),
                ("source", models.CharField(default="heuristic", max_length=32)),
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
                    "case_file",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "Zielakte bei Vorschlägen auf eine bestehende Akte."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="candidates",
                        to="documents.casefile",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="case_file_candidates",
                        to="documents.document",
                    ),
                ),
            ],
            options={
                "verbose_name": "Aktenvorschlag",
                "verbose_name_plural": "Aktenvorschläge",
                "ordering": ["document_id", "status", "-score", "-created_at"],
                "indexes": [
                    models.Index(
                        fields=["document", "status"],
                        name="docs_casecand_doc_status",
                    ),
                    models.Index(
                        fields=["case_file", "status"],
                        name="docs_casecand_case_stat",
                    ),
                ],
                "unique_together": {("document", "signature")},
            },
        ),
    ]
