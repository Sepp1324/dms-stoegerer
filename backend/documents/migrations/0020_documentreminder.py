from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0019_ingest_source_paperless_help_text"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentReminder",
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
                    "remind_on",
                    models.DateField(help_text="Fällig-/Wiedervorlage-Datum"),
                ),
                (
                    "note",
                    models.TextField(
                        blank=True, help_text="Optionale Notiz zur Wiedervorlage"
                    ),
                ),
                (
                    "done",
                    models.BooleanField(
                        default=False,
                        help_text="Erledigt – aus der Wiedervorlage-Liste genommen",
                    ),
                ),
                (
                    "notified_at",
                    models.DateTimeField(
                        blank=True,
                        help_text=(
                            "Wann der Beat diese fällige Erinnerung erstmals "
                            "benachrichtigt hat (genau einmal gesetzt – Dedupe "
                            "gegen Mehrfach-Benachrichtigung)."
                        ),
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_reminders",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reminders",
                        to="documents.document",
                    ),
                ),
            ],
            options={
                "verbose_name": "Erinnerung",
                "verbose_name_plural": "Erinnerungen",
                "ordering": ["remind_on"],
            },
        ),
    ]
