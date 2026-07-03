# Zieht die Model-Drift nach, die der neue CI-Migrations-Check (STOAA-69) auf
# main aufgedeckt hat:
#   1. DocumentVersion.is_immutable – help_text seit STOAA-54 geaendert
#      (0001_initial hatte den alten "wird in Stufe 4 …"-Text).
#   2. MailAccount.id / ProcessedMail.id – in 0005 als AutoField angelegt,
#      obwohl DEFAULT_AUTO_FIELD/apps.default_auto_field = BigAutoField.
# Alle drei Operationen sind reine Feld-Anpassungen (help_text = DB-No-Op,
# AutoField→BigAutoField = ALTER COLUMN auf bigint) und ohne Datenrisiko.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0009_remove_document_email_subject_email_from"),
    ]

    operations = [
        migrations.AlterField(
            model_name="documentversion",
            name="is_immutable",
            field=models.BooleanField(
                default=False,
                help_text="WORM-Flag – nach erfolgreichem process_version() gesetzt",
            ),
        ),
        migrations.AlterField(
            model_name="mailaccount",
            name="id",
            field=models.BigAutoField(
                auto_created=True,
                primary_key=True,
                serialize=False,
                verbose_name="ID",
            ),
        ),
        migrations.AlterField(
            model_name="processedmail",
            name="id",
            field=models.BigAutoField(
                auto_created=True,
                primary_key=True,
                serialize=False,
                verbose_name="ID",
            ),
        ),
    ]
