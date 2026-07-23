from django.db import migrations


def repair_mixed_owner_trees(apps, schema_editor):
    """Trennt bestehende Ordner, deren direkter Parent einem ANDEREN Owner gehört.

    Die alte API erlaubte gemischte Eigentümerbäume. Der neue Serializer-Guard
    verhindert nur NEUE solche Kanten – bereits vorhandene bleiben gefährlich:
    löscht der Parent-Owner seinen Ordner, entfernt CASCADE fremde Unterordner
    (Datenverlust). Daher jede Owner-übergreifende Parent/Child-Kante kappen:
    der Kind-Ordner wird zum Root (parent=NULL). Sein eigener Teilbaum bleibt
    erhalten (dessen Kanten werden separat geprüft). Bei Namenskollision mit einem
    bestehenden Root desselben Owners (unique(owner, name) where parent IS NULL)
    wird ein Zählsuffix angehängt.

    Nicht umkehrbar (reine Datenreparatur) – reverse ist ein No-op.
    """
    DocumentFolder = apps.get_model("documents", "DocumentFolder")

    mixed = (
        DocumentFolder.objects.exclude(parent__isnull=True)
        .select_related("parent")
    )
    for folder in mixed:
        if folder.owner_id == folder.parent.owner_id:
            continue  # single-owner-Kante – ok

        base = folder.name
        name = base
        counter = 1
        while (
            DocumentFolder.objects.filter(
                parent__isnull=True, owner_id=folder.owner_id, name=name
            )
            .exclude(pk=folder.pk)
            .exists()
        ):
            counter += 1
            name = f"{base} ({counter})"

        folder.parent = None
        folder.name = name
        folder.save(update_fields=["parent", "name"])


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0057_folder_root_name_per_owner"),
    ]

    operations = [
        migrations.RunPython(
            repair_mixed_owner_trees, migrations.RunPython.noop
        ),
    ]
