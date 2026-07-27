from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0066_documentsharelink_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentreminder",
            name="email_claimed_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "In-Flight-Lease des E-Mail-Versands (Outbox): Ein Worker setzt "
                    "dies per CAS, BEVOR er sendet. email_sent_at wird erst NACH "
                    "Erfolg gesetzt. Stirbt der Worker dazwischen, läuft die Lease ab "
                    "und ein späterer Lauf versucht erneut – so geht keine Mail "
                    "dauerhaft verloren."
                ),
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="documentreminder",
            name="email_sent_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Wann die Erinnerungs-E-Mail BESTÄTIGT versendet wurde – gesetzt "
                    "ERST nach erfolgreichem SMTP-Versand. Getrennt von notified_at "
                    "(In-App)."
                ),
                null=True,
            ),
        ),
    ]
