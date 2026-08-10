from django.db import migrations, models
import django.db.models.deletion


def backfill_version(apps, schema_editor):
    """Bestehende Markierungen an ``document.current_version`` binden.

    0073 fügte die version-FK nullbar hinzu; bereits vorhandene Markierungen blieben
    NULL und wurden vom (versiongefilterten) GET-Endpoint nicht mehr geliefert. Hier
    werden sie auf die aktuelle Version ihres Dokuments gesetzt. Markierungen, deren
    Dokument keine Version hat (Datenanomalie), werden entfernt – ohne Version sind
    sie bezuglos und blockierten sonst das folgende ``null=False``.
    """
    DocumentHighlight = apps.get_model("documents", "DocumentHighlight")
    orphan_ids = []
    for hl in DocumentHighlight.objects.filter(version__isnull=True).select_related(
        "document"
    ):
        current_id = hl.document.current_version_id
        if current_id:
            hl.version_id = current_id
            hl.save(update_fields=["version"])
        else:
            orphan_ids.append(hl.id)
    if orphan_ids:
        DocumentHighlight.objects.filter(id__in=orphan_ids).delete()


class Migration(migrations.Migration):
    """Backfill der version-FK + Pflichtfeld (Studio Phase 2, Review-Fix).

    RunPython VOR dem AlterField: erst alle NULLs auffüllen, dann ``null=False``
    setzen. Reversibel als No-op (die FK bleibt bestehen; das erneute Nullen wäre
    sinnlos).
    """

    dependencies = [
        ("documents", "0073_documenthighlight_version"),
    ]

    operations = [
        migrations.RunPython(backfill_version, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="documenthighlight",
            name="version",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="highlights",
                to="documents.documentversion",
            ),
        ),
    ]
