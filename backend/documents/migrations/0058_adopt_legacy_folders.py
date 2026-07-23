from django.db import migrations


def adopt_legacy_folders(apps, schema_editor):
    """Weist bestehenden owner=NULL-Ordnern (Migration 0054 ließ sie bewusst
    ownerlos) einen Eigentümer zu, damit sie unter dem neuen Owner-Check wieder
    zuweisbar sind (Detail/Bulk/Drag, Serializer.validate_folder).

    Vorgehen – jeder Baum wird als GANZES betrachtet und bleibt SINGLE-OWNER:
      * Der Eigentümer wird aus den Dokumenten des GESAMTEN Teilbaums (Wurzel +
        alle Nachfahren) ermittelt – nicht nur aus denen, die direkt in der Wurzel
        liegen. Bei ``Akte / Rechnungen / Dokument.pdf`` ist die Wurzel selbst
        meist leer; nur so werden verschachtelte Ordner überhaupt adoptiert.
      * Genau EIN Dokumenteigentümer im Teilbaum -> der ganze Teilbaum geht an ihn.
      * Kein Dokument -> Teilbaum bleibt ownerlos (nichts zu adoptieren).
      * MEHRERE Eigentümer (gemischter Baum) -> Teilbaum bleibt bewusst ownerlos.
        Er ist damit admin-only (nur Admins mutieren owner=NULL-Ordner) – die
        korrekte Zuordnung ist eine manuelle Admin-Triage. So bleibt KEIN
        Minderheitsdokument unter einem fremden Eigentümer hängen (Single-Owner-
        Invariante gewahrt) und kein Fremder kann die Zuordnung per Umbenennen/
        Löschen beeinflussen.

    Kollisionen: Zwei ownerlose Wurzeln gleichen Namens konnten koexistieren
    (unique(owner,name) behandelt NULL als verschieden). Fallen sie beim Adoptieren
    auf DENSELBEN Owner, wird der zweite Name mit Zählsuffix entzerrt.

    Nicht umkehrbar (reine Datenreparatur) – reverse ist ein No-op.
    """
    DocumentFolder = apps.get_model("documents", "DocumentFolder")
    Document = apps.get_model("documents", "Document")

    folders = list(
        DocumentFolder.objects.all().values("id", "parent_id", "owner_id", "name")
    )
    children = {}
    for f in folders:
        children.setdefault(f["parent_id"], []).append(f["id"])

    def subtree_ids(root_id):
        out, stack = [], [root_id]
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(children.get(cur, []))
        return out

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

    roots = [f for f in folders if f["parent_id"] is None and f["owner_id"] is None]
    for root in roots:
        ids = subtree_ids(root["id"])
        owners = list(
            Document.objects.filter(folder_id__in=ids, owner__isnull=False)
            .values_list("owner", flat=True)
            .distinct()
        )
        if len(owners) != 1:
            continue  # 0 -> nichts zu adoptieren; >=2 -> gemischt -> Admin-Triage
        owner_id = owners[0]
        # Nachfahren zuerst (keine per-Owner-Namensbindung -> unkritisch), die
        # Wurzel zuletzt mit kollisionsfreiem Namen (unique(owner,name) an der Wurzel).
        descendants = [i for i in ids if i != root["id"]]
        if descendants:
            DocumentFolder.objects.filter(
                id__in=descendants, owner__isnull=True
            ).update(owner_id=owner_id)
        root_obj = DocumentFolder.objects.get(pk=root["id"])
        root_obj.owner_id = owner_id
        root_obj.name = unique_root_name(owner_id, root_obj.name, root_obj.pk)
        root_obj.save(update_fields=["owner", "name"])


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0057_folder_root_name_per_owner"),
    ]

    operations = [
        migrations.RunPython(adopt_legacy_folders, migrations.RunPython.noop),
    ]
