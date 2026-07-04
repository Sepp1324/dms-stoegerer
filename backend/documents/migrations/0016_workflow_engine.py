# Workflow-Engine (STOAA-263/265): Modelle Workflow / WorkflowTrigger /
# WorkflowAction + DocumentVersion.source (Herkunft für den Trigger-source-Filter).
# Reine Tabellen-Neuanlage + ein Feld mit Spalten-Default ("api") – keine
# Datenmigration, kein Reindex. Deploy: Backend-Image-Rebuild wegen Migration.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0015_documentversion_processing_failure_retry"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="documentversion",
            name="source",
            field=models.CharField(
                choices=[
                    ("upload", "Upload"),
                    ("consume", "Consume-Ordner"),
                    ("mail", "E-Mail"),
                    ("api", "API"),
                ],
                default="api",
                help_text="Herkunft der Version – Grundlage des Workflow-Trigger-source-Filters",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="Workflow",
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
                    "order",
                    models.IntegerField(
                        default=100,
                        help_text="Kleiner = zuerst; Reihenfolge ist deterministisch",
                    ),
                ),
                ("enabled", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "Workflow",
                "verbose_name_plural": "Workflows",
                "ordering": ["order", "name"],
            },
        ),
        migrations.CreateModel(
            name="WorkflowAction",
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
                (
                    "order",
                    models.IntegerField(
                        default=100, help_text="Kleiner = zuerst angewandt"
                    ),
                ),
                (
                    "action_type",
                    models.CharField(
                        choices=[("assign", "Zuweisen"), ("remove", "Entfernen")],
                        max_length=10,
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Titel-Template mit {correspondent}, {created}, {doc_type}",
                        max_length=512,
                    ),
                ),
                (
                    "custom_fields",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="{Zusatzfeld-Name: Wert} – nur für bereits definierte Felder",
                    ),
                ),
                (
                    "correspondent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="documents.correspondent",
                    ),
                ),
                (
                    "document_type",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="documents.documenttype",
                    ),
                ),
                (
                    "storage_path",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="documents.storagepath",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tags",
                    models.ManyToManyField(
                        blank=True,
                        help_text="assign → hinzufügen, remove → entfernen",
                        related_name="+",
                        to="documents.tag",
                    ),
                ),
                (
                    "workflow",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="actions",
                        to="documents.workflow",
                    ),
                ),
            ],
            options={
                "verbose_name": "Workflow-Aktion",
                "verbose_name_plural": "Workflow-Aktionen",
                "ordering": ["order", "id"],
            },
        ),
        migrations.CreateModel(
            name="WorkflowTrigger",
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
                (
                    "trigger_type",
                    models.CharField(
                        choices=[
                            ("document_added", "Dokument hinzugefügt"),
                            ("document_updated", "Dokument aktualisiert"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "source",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="Teilmenge von {upload,consume,mail,api}; leer = jede Quelle",
                    ),
                ),
                (
                    "filter_path",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Glob gegen den Dateipfad der Version (z. B. *.pdf, inbox/*)",
                        max_length=512,
                    ),
                ),
                (
                    "text_contains",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="ODER-Wortliste (oder Einzelwort); via rule_matches gegen den Text",
                    ),
                ),
                (
                    "text_regex",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "filter_correspondent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="documents.correspondent",
                    ),
                ),
                (
                    "filter_document_type",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="documents.documenttype",
                    ),
                ),
                (
                    "filter_has_tags",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Alle diese Tags müssen vorhanden sein",
                        related_name="+",
                        to="documents.tag",
                    ),
                ),
                (
                    "filter_has_not_tags",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Keiner dieser Tags darf vorhanden sein",
                        related_name="+",
                        to="documents.tag",
                    ),
                ),
                (
                    "workflow",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="triggers",
                        to="documents.workflow",
                    ),
                ),
            ],
            options={
                "verbose_name": "Workflow-Trigger",
                "verbose_name_plural": "Workflow-Trigger",
            },
        ),
    ]
