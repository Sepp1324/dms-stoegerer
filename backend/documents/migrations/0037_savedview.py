from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0036_merge_0035_archive_retention_center_0035_dossier"),
    ]

    operations = [
        migrations.CreateModel(
            name="SavedView",
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
                ("name", models.CharField(max_length=120)),
                ("description", models.CharField(blank=True, default="", max_length=255)),
                ("query", models.JSONField(blank=True, default=dict)),
                ("is_default", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="saved_views",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Gespeicherte Ansicht",
                "verbose_name_plural": "Gespeicherte Ansichten",
                "ordering": ["name"],
            },
        ),
        migrations.AddConstraint(
            model_name="savedview",
            constraint=models.UniqueConstraint(
                fields=("owner", "name"), name="docs_saved_view_owner_name"
            ),
        ),
        migrations.AddConstraint(
            model_name="savedview",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_default", True)),
                fields=("owner",),
                name="docs_saved_view_one_default",
            ),
        ),
        migrations.AddIndex(
            model_name="savedview",
            index=models.Index(fields=["owner", "name"], name="docs_sv_owner_name_idx"),
        ),
    ]
