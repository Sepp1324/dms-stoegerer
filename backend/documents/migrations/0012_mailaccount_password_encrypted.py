# Mail Account API (STOAA-212): Das Passwort-Feld wird von CharField(255) auf
# TextField umgestellt, damit die Fernet-Chiffretexte (deutlich länger als der
# Klartext) hineinpassen. Verschlüsselt wird beim Speichern in MailAccount.save()
# – Alt-Klartext bleibt lesbar (decrypt_secret fällt bei Nicht-Token auf den
# Rohwert zurück), daher ist keine Datenmigration nötig.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0011_documentsharelink"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mailaccount",
            name="password",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Alternativ direkt hinterlegtes App-Passwort (nur ohne "
                    "Secret-Env). Wird beim Speichern verschlüsselt (Fernet, siehe "
                    "crypto.py) – niemals im Klartext in der DB und niemals über die "
                    "API ausgegeben."
                ),
            ),
        ),
    ]
