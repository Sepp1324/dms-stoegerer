from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0013_documentversion_ocr_status_tracking"),
    ]

    operations = [
        migrations.AddField(
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
                ],
                db_index=True,
                default="uploaded",
                help_text="State Machine der Dokumentverarbeitung (uploaded → ready)",
                max_length=32,
            ),
        ),
    ]
