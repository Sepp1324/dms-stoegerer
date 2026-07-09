from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0031_documentreviewtask"),
    ]

    operations = [
        migrations.AlterField(
            model_name="documentreviewtask",
            name="kind",
            field=models.CharField(
                choices=[
                    ("metadata_missing", "Metadaten fehlen"),
                    ("ocr_failed", "OCR fehlgeschlagen"),
                    ("ocr_empty", "OCR leer/schwach"),
                    ("classification_low_confidence", "Klassifizierung unsicher"),
                    ("ai_suggestion_pending", "KI-Vorschlag prüfen"),
                    ("extraction_pending", "Strukturdaten prüfen"),
                    ("case_file_pending", "Aktenvorschlag prüfen"),
                    ("contract_review", "Vertrag prüfen"),
                    ("duplicate_suspected", "Dublettenverdacht"),
                    ("asn_missing", "ASN fehlt"),
                    ("email_needs_review", "E-Mail prüfen"),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
        migrations.CreateModel(
            name="ContractRecord",
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
                    "contract_type",
                    models.CharField(
                        choices=[
                            ("insurance", "Versicherung"),
                            ("energy", "Energie"),
                            ("telecom", "Telekom"),
                            ("rent", "Miete"),
                            ("loan", "Kredit"),
                            ("subscription", "Abo"),
                            ("public", "Behörde"),
                            ("other", "Sonstiges"),
                        ],
                        default="other",
                        max_length=24,
                    ),
                ),
                ("provider", models.CharField(blank=True, default="", max_length=255)),
                (
                    "contract_number",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                (
                    "amount",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                ("currency", models.CharField(default="EUR", max_length=3)),
                (
                    "billing_cycle",
                    models.CharField(
                        choices=[
                            ("monthly", "Monatlich"),
                            ("quarterly", "Quartalsweise"),
                            ("yearly", "Jährlich"),
                            ("one_time", "Einmalig"),
                            ("unknown", "Unklar"),
                        ],
                        default="unknown",
                        max_length=16,
                    ),
                ),
                ("starts_on", models.DateField(blank=True, null=True)),
                ("ends_on", models.DateField(blank=True, null=True)),
                ("notice_period_days", models.PositiveIntegerField(blank=True, null=True)),
                ("cancel_until", models.DateField(blank=True, null=True)),
                ("next_due_on", models.DateField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Aktiv"),
                            ("canceled", "Gekündigt"),
                            ("expired", "Abgelaufen"),
                            ("unclear", "Unklar"),
                        ],
                        db_index=True,
                        default="unclear",
                        max_length=16,
                    ),
                ),
                ("confidence", models.PositiveSmallIntegerField(default=0)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("heuristic", "Heuristik"),
                            ("ai", "KI"),
                            ("manual", "Manuell"),
                            ("rule", "Regel"),
                        ],
                        default="heuristic",
                        max_length=16,
                    ),
                ),
                ("needs_review", models.BooleanField(db_index=True, default=True)),
                ("notes", models.TextField(blank=True, default="")),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "case_file",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="contract_records",
                        to="documents.casefile",
                    ),
                ),
                (
                    "document",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="contract_record",
                        to="documents.document",
                    ),
                ),
                (
                    "extracted_from_version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="contract_records",
                        to="documents.documentversion",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_contract_records",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Vertrag",
                "verbose_name_plural": "Verträge",
                "ordering": ["needs_review", "cancel_until", "next_due_on", "provider"],
                "indexes": [
                    models.Index(
                        fields=["status", "next_due_on"],
                        name="docs_contract_status_due",
                    ),
                    models.Index(
                        fields=["needs_review", "status"],
                        name="docs_contract_review",
                    ),
                    models.Index(
                        fields=["cancel_until"],
                        name="docs_contract_cancel",
                    ),
                ],
            },
        ),
    ]
