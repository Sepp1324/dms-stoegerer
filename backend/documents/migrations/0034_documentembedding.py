import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0033_knowledgeentity_entityidentifier_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentEmbedding",
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
                ("page_no", models.PositiveIntegerField(blank=True, null=True)),
                ("chunk_index", models.PositiveIntegerField()),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("page_text", "Seitentext"),
                            ("ocr_text", "OCR-Text"),
                            ("metadata", "Metadaten"),
                        ],
                        default="page_text",
                        max_length=16,
                    ),
                ),
                ("text", models.TextField()),
                ("text_hash", models.CharField(max_length=64)),
                ("embedding_model", models.CharField(default="local-hash-v1", max_length=64)),
                ("dimension", models.PositiveSmallIntegerField(default=192)),
                ("vector", models.JSONField(default=list)),
                ("magnitude", models.FloatField(default=0.0)),
                ("token_count", models.PositiveIntegerField(default=0)),
                ("generated_at", models.DateTimeField(auto_now_add=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="semantic_chunks",
                        to="documents.document",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="semantic_chunks",
                        to="documents.documentversion",
                    ),
                ),
            ],
            options={
                "verbose_name": "Semantischer Chunk",
                "verbose_name_plural": "Semantische Chunks",
                "ordering": ["document_id", "chunk_index"],
            },
        ),
        migrations.AddConstraint(
            model_name="documentembedding",
            constraint=models.UniqueConstraint(
                fields=("version", "embedding_model", "chunk_index"),
                name="docs_emb_ver_model_chunk",
            ),
        ),
        migrations.AddIndex(
            model_name="documentembedding",
            index=models.Index(
                fields=["document", "embedding_model"], name="docs_emb_doc_model"
            ),
        ),
        migrations.AddIndex(
            model_name="documentembedding",
            index=models.Index(fields=["version"], name="docs_emb_version"),
        ),
        migrations.AddIndex(
            model_name="documentembedding",
            index=models.Index(
                fields=["embedding_model", "-generated_at"], name="docs_emb_model_time"
            ),
        ),
    ]
