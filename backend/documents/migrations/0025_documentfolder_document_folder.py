from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0024_document_review_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentFolder",
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
                ("name", models.CharField(max_length=255)),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="children",
                        to="documents.documentfolder",
                    ),
                ),
            ],
            options={
                "verbose_name": "Ordner",
                "verbose_name_plural": "Ordner",
                "ordering": ["parent__name", "name"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("parent", "name"),
                        name="documents_folder_unique_sibling_name",
                    ),
                    models.UniqueConstraint(
                        condition=Q(("parent__isnull", True)),
                        fields=("name",),
                        name="documents_folder_unique_root_name",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="document",
            name="folder",
            field=models.ForeignKey(
                blank=True,
                help_text="Fachlicher Ordner/Akte für die UI-Navigation.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="documents",
                to="documents.documentfolder",
            ),
        ),
    ]
