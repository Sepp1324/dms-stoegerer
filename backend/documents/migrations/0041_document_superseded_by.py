import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Soft-Merge von Dubletten: superseded_by-Self-FK + superseded_at.

    Rein additiv (beide Felder null/blank) – gefahrlos auf bestehendem PVC.
    """

    dependencies = [
        ("documents", "0040_documentchunk"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="superseded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="supersedes",
                to="documents.document",
                help_text="Als Dublette ausgeblendet – ersetzt durch dieses (kanonische) Dokument.",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="superseded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
