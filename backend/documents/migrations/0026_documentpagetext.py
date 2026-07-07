from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0025_documentfolder_document_folder"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentPageText",
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
                ("text", models.TextField(blank=True)),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="page_texts",
                        to="documents.documentversion",
                    ),
                ),
            ],
            options={
                "verbose_name": "Seitentext",
                "verbose_name_plural": "Seitentexte",
                "ordering": ["version_id", "page_no"],
                "unique_together": {("version", "page_no")},
            },
        ),
    ]
