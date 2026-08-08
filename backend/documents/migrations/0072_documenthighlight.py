from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Positions-verankerte Markierungen/Notizen am Beleg (Studio Phase 2).

    Additiv/PVC-sicher: neue Tabelle ``DocumentHighlight`` (separate, abgeleitete
    Anzeige-Daten; das WORM-Original bleibt unberührt).
    """

    dependencies = [
        ("documents", "0071_page_layout_and_extraction_anchor"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentHighlight",
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
                ("bbox", models.JSONField()),
                ("note", models.TextField(blank=True)),
                ("color", models.CharField(blank=True, max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="highlights",
                        to="documents.document",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Markierung",
                "verbose_name_plural": "Markierungen",
                "ordering": ["document_id", "page_no", "created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="documenthighlight",
            index=models.Index(
                fields=["document", "page_no"], name="documents_hl_doc_page_idx"
            ),
        ),
    ]
