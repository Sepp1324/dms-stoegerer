from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0034_documentembedding"),
    ]

    operations = [
        migrations.CreateModel(
            name="Dossier",
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
                (
                    "query",
                    models.TextField(
                        help_text="Frage/Thema, aus dem das Dossier erzeugt wird."
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Entwurf"),
                            ("generated", "Generiert"),
                            ("final", "Final"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=16,
                    ),
                ),
                ("summary", models.TextField(blank=True, default="")),
                ("timeline", models.JSONField(blank=True, default=list)),
                ("sources", models.JSONField(blank=True, default=list)),
                ("entities", models.JSONField(blank=True, default=list)),
                ("contracts", models.JSONField(blank=True, default=list)),
                (
                    "generated_source",
                    models.CharField(
                        choices=[
                            ("local", "Lokal"),
                            ("ai", "KI"),
                            ("unavailable", "KI nicht verfügbar"),
                            ("error", "KI-Fehler"),
                        ],
                        default="local",
                        max_length=24,
                    ),
                ),
                ("generated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "documents",
                    models.ManyToManyField(
                        blank=True, related_name="dossiers", to="documents.document"
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dossiers",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Dossier",
                "verbose_name_plural": "Dossiers",
                "ordering": ["-updated_at", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="dossier",
            index=models.Index(fields=["owner", "status"], name="docs_dossier_owner_status"),
        ),
        migrations.AddIndex(
            model_name="dossier",
            index=models.Index(fields=["-updated_at"], name="docs_dossier_updated"),
        ),
    ]
