import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("documents", "0005_mailaccount_processedmail"),
    ]

    operations = [
        migrations.AddField(
            model_name="mailaccount",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Standard-Empfänger: Eigentümer der aus diesem Postfach "
                    "importierten Dokumente. Leer lassen = Admin-Triage-Postfach "
                    "(nur für DMS-Admins sichtbar, bis manuell zugeordnet)."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="mail_accounts",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
