from django.db import migrations, models


class Migration(migrations.Migration):
    """Additiv: getrennter Versandstatus für Erinnerungs-E-Mails, damit ein
    fehlgeschlagener Versand erneut versucht werden kann (entkoppelt vom
    In-App-Dedupe notified_at)."""

    dependencies = [
        ("documents", "0052_flashcardsyncentry_last_error"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentreminder",
            name="email_sent_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text=(
                    "Wann die Erinnerungs-E-Mail BESTÄTIGT versendet wurde. Getrennt von "
                    "notified_at (In-App), damit ein fehlgeschlagener Versand erneut "
                    "versucht wird und nicht am In-App-Dedupe hängen bleibt."
                ),
            ),
        ),
    ]
