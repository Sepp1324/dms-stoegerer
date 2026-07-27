import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0065_artifactcleanupjob"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentsharelink",
            name="version",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Gepinnte Version, die dieser Link ausliefert. NULL = immer "
                    "die aktuelle Version des Dokuments."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="share_links",
                to="documents.documentversion",
            ),
        ),
    ]
