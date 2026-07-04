from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0014_documentversion_processing_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentversion",
            name="processing_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="documentversion",
            name="processing_failed_step",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="documentversion",
            name="processing_failed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documentversion",
            name="processing_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="documentversion",
            name="processing_state",
            field=models.CharField(
                choices=[
                    ("uploaded", "Uploaded"),
                    ("hashed", "Hashed"),
                    ("ocr_running", "OCR running"),
                    ("ocr_done", "OCR done"),
                    ("classification_running", "Classification running"),
                    ("classified", "Classified"),
                    ("thumbnail_done", "Thumbnail done"),
                    ("sealed", "Sealed"),
                    ("ready", "Ready"),
                    ("failed", "Failed"),
                    ("retry_pending", "Retry pending"),
                ],
                db_index=True,
                default="uploaded",
                help_text="State Machine der Dokumentverarbeitung (uploaded → ready)",
                max_length=32,
            ),
        ),
    ]
