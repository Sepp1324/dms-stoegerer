from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Additiv: Eigentümer an Workflow und ClassificationRule (Owner-Scoping).

    Bestehende Workflows/Regeln bleiben ownerlos (null = global) und wirken wie
    bisher – dürfen aber künftig nur von Admins verwaltet werden. Neue, von
    Nicht-Admins angelegte Workflows/Regeln bekommen einen Owner und wirken nur
    auf dessen Dokumente.
    """

    dependencies = [
        ("documents", "0052_flashcardsyncentry_last_error"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="workflow",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="owned_workflows",
                to=settings.AUTH_USER_MODEL,
                help_text="Eigentümer – wirkt nur auf dessen Dokumente; null = global (Admin).",
            ),
        ),
        migrations.AddField(
            model_name="classificationrule",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="owned_classification_rules",
                to=settings.AUTH_USER_MODEL,
                help_text="Eigentümer – wirkt nur auf dessen Dokumente; null = global (Admin).",
            ),
        ),
    ]
