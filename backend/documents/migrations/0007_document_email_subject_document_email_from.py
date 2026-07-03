from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0006_mailaccount_owner"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="email_subject",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="document",
            name="email_from",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
    ]
