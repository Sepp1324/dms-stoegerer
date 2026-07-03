import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0004_document_classification"),
    ]

    operations = [
        migrations.CreateModel(
            name="MailAccount",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Bezeichnung, z. B. 'Rechnungen'", max_length=255
                    ),
                ),
                ("host", models.CharField(max_length=255)),
                ("port", models.PositiveIntegerField(default=993)),
                (
                    "use_ssl",
                    models.BooleanField(
                        default=True,
                        help_text="IMAPS (i. d. R. Port 993). Aus = unverschlüsselt/STARTTLS.",
                    ),
                ),
                ("username", models.CharField(max_length=255)),
                ("folder", models.CharField(default="INBOX", max_length=255)),
                (
                    "password_env",
                    models.CharField(
                        blank=True,
                        help_text="Name der Umgebungsvariable (k8s-Secret) mit dem Passwort – empfohlen.",
                        max_length=255,
                    ),
                ),
                (
                    "password",
                    models.CharField(
                        blank=True,
                        help_text="Alternativ direkt hinterlegtes App-Passwort (nur ohne Secret-Env).",
                        max_length=255,
                    ),
                ),
                ("enabled", models.BooleanField(default=True)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
            ],
            options={
                "verbose_name": "E-Mail-Konto",
                "verbose_name_plural": "E-Mail-Konten",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="ProcessedMail",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "message_id",
                    models.CharField(
                        help_text="RFC-822 Message-ID-Header", max_length=998
                    ),
                ),
                ("subject", models.CharField(blank=True, max_length=512)),
                ("sender", models.CharField(blank=True, max_length=512)),
                ("attachment_count", models.PositiveIntegerField(default=0)),
                ("imported_count", models.PositiveIntegerField(default=0)),
                ("processed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="processed_mails",
                        to="documents.mailaccount",
                    ),
                ),
            ],
            options={
                "verbose_name": "Verarbeitete E-Mail",
                "verbose_name_plural": "Verarbeitete E-Mails",
                "ordering": ["-processed_at"],
                "unique_together": {("account", "message_id")},
            },
        ),
    ]
