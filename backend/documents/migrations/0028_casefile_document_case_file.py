import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0027_extractioncandidate"),
    ]

    operations = [
        migrations.CreateModel(
            name="CaseFile",
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
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Aktiv"),
                            ("waiting", "Wartet"),
                            ("done", "Erledigt"),
                            ("archived", "Archiviert"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=16,
                    ),
                ),
                ("ai_summary", models.TextField(blank=True, default="")),
                ("ai_summary_source", models.CharField(blank=True, default="", max_length=32)),
                ("ai_summary_generated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="case_files",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Vorgang",
                "verbose_name_plural": "Vorgänge",
                "ordering": ["status", "-updated_at", "title"],
            },
        ),
        migrations.AddField(
            model_name="document",
            name="case_file",
            field=models.ForeignKey(
                blank=True,
                help_text="Fachlicher Vorgang/Akte, dem das Dokument zugeordnet ist.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="documents",
                to="documents.casefile",
            ),
        ),
        migrations.AddIndex(
            model_name="casefile",
            index=models.Index(
                fields=["owner", "status"],
                name="documents_case_owner_status_idx",
            ),
        ),
    ]
