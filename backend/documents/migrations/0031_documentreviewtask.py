from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0030_processedmail_email_center"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentReviewTask",
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
                            ("metadata_missing", "Metadaten fehlen"),
                            ("ocr_failed", "OCR fehlgeschlagen"),
                            ("ocr_empty", "OCR leer/schwach"),
                            (
                                "classification_low_confidence",
                                "Klassifizierung unsicher",
                            ),
                            ("ai_suggestion_pending", "KI-Vorschlag prüfen"),
                            ("extraction_pending", "Strukturdaten prüfen"),
                            ("case_file_pending", "Aktenvorschlag prüfen"),
                            ("duplicate_suspected", "Dublettenverdacht"),
                            ("asn_missing", "ASN fehlt"),
                            ("email_needs_review", "E-Mail prüfen"),
                        ],
                        db_index=True,
                        max_length=40,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Offen"),
                            ("resolved", "Erledigt"),
                            ("ignored", "Ignoriert"),
                        ],
                        db_index=True,
                        default="open",
                        max_length=16,
                    ),
                ),
                (
                    "signature",
                    models.CharField(
                        help_text="Idempotenz-Schlüssel pro Dokument/Klärungsgrund.",
                        max_length=160,
                    ),
                ),
                ("priority", models.PositiveSmallIntegerField(db_index=True, default=50)),
                ("message", models.CharField(max_length=255)),
                (
                    "suggested_action",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("data", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="review_tasks",
                        to="documents.document",
                    ),
                ),
                (
                    "resolved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="resolved_review_tasks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Klärungsauftrag",
                "verbose_name_plural": "Klärungsaufträge",
                "ordering": ["status", "priority", "created_at"],
                "indexes": [
                    models.Index(
                        fields=["document", "status"],
                        name="docs_revtask_doc_status",
                    ),
                    models.Index(
                        fields=["status", "priority"],
                        name="docs_revtask_status_prio",
                    ),
                    models.Index(
                        fields=["kind", "status"],
                        name="docs_revtask_kind_status",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("document", "signature"),
                        name="docs_revtask_sig_uniq",
                    ),
                ],
            },
        ),
    ]
