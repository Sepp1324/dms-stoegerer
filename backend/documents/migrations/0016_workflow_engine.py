from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0015_documentversion_processing_failure_retry"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ingest_source auf DocumentVersion
        migrations.AddField(
            model_name="documentversion",
            name="ingest_source",
            field=models.CharField(
                blank=True,
                default="upload",
                help_text="upload | consume | mail | api",
                max_length=16,
            ),
        ),
        # Workflow
        migrations.CreateModel(
            name="Workflow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("order", models.IntegerField(default=100, help_text="Kleiner = früher ausgeführt")),
                ("enabled", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "Workflow",
                "verbose_name_plural": "Workflows",
                "ordering": ["order", "name"],
            },
        ),
        # WorkflowTrigger
        migrations.CreateModel(
            name="WorkflowTrigger",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("trigger_type", models.CharField(
                    choices=[("document_added", "Dokument hinzugefügt"), ("document_updated", "Dokument aktualisiert")],
                    default="document_added",
                    max_length=32,
                )),
                ("sources", models.CharField(
                    blank=True,
                    default="",
                    help_text="Kommagetrennte Liste: upload,consume,mail,api – leer = alle",
                    max_length=255,
                )),
                ("filter_path", models.CharField(blank=True, default="", max_length=512,
                    help_text="Glob gegen den Dateipfad der Version (optional)")),
                ("filter_text_contains", models.CharField(blank=True, default="", max_length=512)),
                ("filter_text_regex", models.CharField(blank=True, default="", max_length=512)),
                ("workflow", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="trigger",
                    to="documents.workflow",
                )),
                ("filter_correspondent", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to="documents.correspondent",
                )),
                ("filter_document_type", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to="documents.documenttype",
                )),
                ("filter_has_tags", models.ManyToManyField(
                    blank=True,
                    related_name="trigger_has",
                    to="documents.tag",
                    help_text="Dokument muss ALLE diese Tags haben",
                )),
                ("filter_has_not_tags", models.ManyToManyField(
                    blank=True,
                    related_name="trigger_has_not",
                    to="documents.tag",
                    help_text="Dokument darf KEINEN dieser Tags haben",
                )),
            ],
            options={
                "verbose_name": "Workflow-Trigger",
                "verbose_name_plural": "Workflow-Trigger",
            },
        ),
        # WorkflowAction
        migrations.CreateModel(
            name="WorkflowAction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.IntegerField(default=10)),
                ("action_type", models.CharField(
                    choices=[("assign", "Zuweisen"), ("remove", "Entfernen")],
                    default="assign",
                    max_length=16,
                )),
                ("assign_title", models.CharField(blank=True, default="", max_length=512,
                    help_text="Titel-Template: {correspondent}, {created}, {doc_type} erlaubt")),
                ("assign_custom_fields", models.JSONField(blank=True, default=dict)),
                ("workflow", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="actions",
                    to="documents.workflow",
                )),
                ("assign_correspondent", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to="documents.correspondent",
                )),
                ("assign_document_type", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to="documents.documenttype",
                )),
                ("assign_storage_path", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to="documents.storagepath",
                )),
                ("assign_owner", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("assign_tags", models.ManyToManyField(
                    blank=True,
                    related_name="action_assign",
                    to="documents.tag",
                    help_text="Tags ergänzen",
                )),
                ("remove_tags", models.ManyToManyField(
                    blank=True,
                    related_name="action_remove",
                    to="documents.tag",
                    help_text="Tags entfernen",
                )),
            ],
            options={
                "verbose_name": "Workflow-Aktion",
                "verbose_name_plural": "Workflow-Aktionen",
                "ordering": ["order"],
            },
        ),
    ]
