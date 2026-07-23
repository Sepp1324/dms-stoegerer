from django.db import migrations
from django.db.models import Count


def adopt_legacy_folders(apps, schema_editor):
    """Weist bestehenden owner=NULL-Ordnern (Migration 0054 ließ sie bewusst
    ownerlos) einen Eigentümer zu, damit sie unter dem neuen Owner-Check wieder
    zuweisbar sind (Detail/Bulk/Drag-and-drop, Serializer.validate_folder).

    Strategie – jeder Baum wird SINGLE-OWNER (Voraussetzung des Owner-Modells):
      * Wurzel-Ordner: Eigentümer = Mehrheits-Dokumenteigentümer der direkt darin
        liegenden Dokumente (deterministischer Tie-Break über die kleinste ID).
      * Unterordner: erben den Eigentümer ihres Parents (in Baum-Reihenfolge),
        damit kein gemischter Baum entsteht (CASCADE bliebe sonst owner-übergreifend).
      * Wurzeln ganz OHNE Dokumente bleiben ownerlos (admin-only, global) – sie
        blockieren keine bestehende Zuordnung.

    Kollisionen: Zwei ownerlose Wurzeln gleichen Namens konnten koexistieren
    (unique(owner,name) behandelt NULL als verschieden). Fallen sie beim Adoptieren
    auf DENSELBEN Owner, wird der zweite Name mit Zählsuffix entzerrt.

    Nicht umkehrbar (reine Datenreparatur) – reverse ist ein No-op.
    """
    DocumentFolder = apps.get_model("documents", "DocumentFolder")
    Document = apps.get_model("documents", "Document")

    def majority_owner(folder_pk):
        row = (
            Document.objects.filter(folder_id=folder_pk, owner__isnull=False)
            .values("owner")
            .annotate(n=Count("id"))
            .order_by("-n", "owner")
            .first()
        )
        return row["owner"] if row else None

    def unique_root_name(owner_id, name, pk):
        base, candidate, i = name, name, 1
        while (
            DocumentFolder.objects.filter(
                parent__isnull=True, owner_id=owner_id, name=candidate
            )
            .exclude(pk=pk)
            .exists()
        ):
            i += 1
            candidate = f"{base} ({i})"
        return candidate

    # Wiederholt, bis keine Zuweisung mehr möglich ist: pro Runde jede ownerlose
    # Wurzel (per Mehrheit) bzw. jedes Kind mit inzwischen bekanntem Parent-Owner.
    changed = True
    while changed:
        changed = False
        for folder in DocumentFolder.objects.filter(
            owner__isnull=True
        ).select_related("parent"):
            if folder.parent_id is not None:
                if folder.parent.owner_id is not None:
                    folder.owner_id = folder.parent.owner_id
                    folder.save(update_fields=["owner"])
                    changed = True
                continue  # Parent noch NULL -> spätere Runde
            owner_id = majority_owner(folder.pk)
            if owner_id is not None:
                folder.owner_id = owner_id
                folder.name = unique_root_name(owner_id, folder.name, folder.pk)
                folder.save(update_fields=["owner", "name"])
                changed = True


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0057_folder_root_name_per_owner"),
    ]

    operations = [
        migrations.RunPython(adopt_legacy_folders, migrations.RunPython.noop),
    ]
