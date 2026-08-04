from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Datenfundament fürs visuelle Dokument-Studio (Phase 1).

    Additiv/PVC-sicher: neue Tabelle ``DocumentPageLayout`` (wortgenaue
    OCR-Geometrie, separat gehalten – schlanke Version-Rows) und zwei nullbare
    Verankerungsfelder auf ``ExtractionCandidate`` (Fundstelle im PDF).
    """

    dependencies = [
        ("documents", "0070_auditlog_trigger_actor_lock"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentPageLayout",
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
                ("page_no", models.PositiveIntegerField()),
                ("width", models.FloatField(default=0.0)),
                ("height", models.FloatField(default=0.0)),
                ("words", models.JSONField(blank=True, default=list)),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="page_layouts",
                        to="documents.documentversion",
                    ),
                ),
            ],
            options={
                "verbose_name": "Seiten-Layout",
                "verbose_name_plural": "Seiten-Layouts",
                "ordering": ["version_id", "page_no"],
                "unique_together": {("version", "page_no")},
            },
        ),
        migrations.AddField(
            model_name="extractioncandidate",
            name="source_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="documents.documentversion",
            ),
        ),
        migrations.AddField(
            model_name="extractioncandidate",
            name="source_bbox",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
