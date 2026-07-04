# Freigabelinks (STOAA-190): neue Tabelle DocumentShareLink – Fundament der
# Freigabelink-Kette (STOAA-96/STOAA-187), Login-Pflicht-Variante mit
# Pflicht-Ablauf. Reine Tabellen-Neuanlage: keine Datenmigration, kein Reindex.
#
#   * token_hash: unique (SHA-256-Hex) – nur der Hash wird gespeichert.
#   * expires_at: NOT NULL – ein Freigabelink gilt nie unbegrenzt.
#   * revoked_at: nullbar – Soft-Widerruf.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0010_alter_documentversion_is_immutable_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentShareLink",
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
                    "token_hash",
                    models.CharField(
                        help_text=(
                            "SHA-256-Hex des Freigabe-Tokens. NUR der Hash wird "
                            "gespeichert, nie der Klartext."
                        ),
                        max_length=64,
                        unique=True,
                    ),
                ),
                (
                    "expires_at",
                    models.DateTimeField(
                        help_text="Pflicht-Ablauf – ein Freigabelink gilt nie unbegrenzt."
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "revoked_at",
                    models.DateTimeField(
                        blank=True,
                        help_text=(
                            "Zeitpunkt des Widerrufs (Soft-Delete); gesetzt → "
                            "is_valid=False."
                        ),
                        null=True,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_share_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="share_links",
                        to="documents.document",
                    ),
                ),
            ],
            options={
                "verbose_name": "Freigabelink",
                "verbose_name_plural": "Freigabelinks",
                "ordering": ["-created_at"],
            },
        ),
    ]
