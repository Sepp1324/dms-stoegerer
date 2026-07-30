from django.db import migrations, models


def backfill_postprocessed_at(apps, schema_editor):
    """Bestehende READY-Versionen als bereits nachbearbeitet markieren (P1).

    Die alte Nachbearbeitung (Vertragsabgleich/Entity-Graph/Auto-Ablage/Review)
    lief inline NACH READY. Ohne diesen Backfill hätte JEDE Bestands-READY-Version
    ``postprocessed_at IS NULL`` und der neue Reconciler
    (``reap_unpostprocessed_versions``) würde beim ersten Lauf den GESAMTEN Bestand
    erneut nachbearbeiten. Wir setzen den Marker daher auf den Zeitpunkt des
    READY-Übergangs.
    """
    DocumentVersion = apps.get_model("documents", "DocumentVersion")
    DocumentVersion.objects.filter(
        processing_state="ready", postprocessed_at__isnull=True
    ).update(postprocessed_at=models.F("processing_state_changed_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0067_documentreminder_email_claimed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentversion",
            name="postprocessed_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(backfill_postprocessed_at, migrations.RunPython.noop),
    ]
