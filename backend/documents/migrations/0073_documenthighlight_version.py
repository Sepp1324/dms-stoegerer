from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Bindet Markierungen an eine konkrete DocumentVersion (Studio Phase 2).

    Additiv/PVC-sicher: nullbare FK (nur der leere Altbestand bleibt ohne Version;
    neue Markierungen tragen immer eine).
    """

    dependencies = [
        ("documents", "0072_documenthighlight"),
    ]

    operations = [
        migrations.AddField(
            model_name="documenthighlight",
            name="version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="highlights",
                to="documents.documentversion",
            ),
        ),
    ]
