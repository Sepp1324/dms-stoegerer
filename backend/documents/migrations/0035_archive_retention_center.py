import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0034_documentembedding"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="archive_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="archive_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="document",
            name="archive_report",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="document",
            name="archive_status",
            field=models.CharField(
                choices=[
                    ("unchecked", "Nicht geprüft"),
                    ("ok", "OK"),
                    ("warning", "Warnung"),
                    ("error", "Fehler"),
                ],
                db_index=True,
                default="unchecked",
                help_text="Letzter Ergebnisstatus der Archiv-/Integritätsprüfung.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="legal_hold",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Manuelle Sperre: Dokument darf unabhängig von Retention nicht gelöscht werden.",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="legal_hold_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="document",
            name="legal_hold_set_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="legal_hold_set_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="legal_hold_documents",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
