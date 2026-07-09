from django.db import migrations, models


def backfill_mail_status(apps, schema_editor):
    ProcessedMail = apps.get_model("documents", "ProcessedMail")
    for item in ProcessedMail.objects.all().only(
        "id", "attachment_count", "imported_count", "status"
    ):
        if item.imported_count == 0:
            status = "ignored"
        elif item.imported_count < item.attachment_count:
            status = "partial"
        else:
            status = "imported"
        ProcessedMail.objects.filter(pk=item.pk).update(status=status)


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0029_casefilecandidate"),
    ]

    operations = [
        migrations.AddField(
            model_name="processedmail",
            name="attachment_names",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="processedmail",
            name="documents",
            field=models.ManyToManyField(
                blank=True,
                help_text="Dokumente, die aus Anhängen dieser Mail entstanden sind.",
                related_name="source_mails",
                to="documents.document",
            ),
        ),
        migrations.AddField(
            model_name="processedmail",
            name="error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="processedmail",
            name="note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="processedmail",
            name="received_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="processedmail",
            name="status",
            field=models.CharField(
                choices=[
                    ("imported", "Importiert"),
                    ("partial", "Teilweise importiert"),
                    ("ignored", "Ignoriert"),
                    ("failed", "Fehlerhaft"),
                ],
                db_index=True,
                default="imported",
                max_length=16,
            ),
        ),
        migrations.AddIndex(
            model_name="processedmail",
            index=models.Index(
                fields=["account", "status"],
                name="docs_mail_account_status",
            ),
        ),
        migrations.AddIndex(
            model_name="processedmail",
            index=models.Index(
                fields=["status", "-processed_at"],
                name="docs_mail_status_time",
            ),
        ),
        migrations.RunPython(backfill_mail_status, migrations.RunPython.noop),
    ]
