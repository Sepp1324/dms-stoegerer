import django.db.models.deletion
import pgvector.django
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0039_vector_extension"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentChunk",
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
                ("chunk_index", models.PositiveIntegerField(default=0)),
                ("text", models.TextField()),
                (
                    "embedding",
                    pgvector.django.VectorField(
                        blank=True, dimensions=1024, null=True
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="documents.document",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="documents.documentversion",
                    ),
                ),
            ],
            options={
                "verbose_name": "Dokument-Chunk",
                "verbose_name_plural": "Dokument-Chunks",
                "ordering": ["document_id", "chunk_index"],
            },
        ),
    ]
