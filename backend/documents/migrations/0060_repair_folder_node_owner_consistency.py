from django.db import migrations


def repair_folder_node_owner_consistency(apps, schema_editor):
    """Idempotente Reparatur – berücksichtigt jetzt auch die Eigentümer der
    ORDNERKNOTEN, nicht nur der enthaltenen Dokumente.

    0059 betrachtete nur die Dokument-Eigentümer im Teilbaum. Ein gemischter Baum
    ohne (oder nur mit gleich-owner) Dokumenten – z. B. ein Admin legt seinen
    Unterordner unter Alices Root – blieb dadurch unentdeckt: Alice könnte ihre
    Root löschen und CASCADE risse den Admin-Unterordner mit. Diese Migration zieht
    zusätzlich die Ordner-Owner heran und macht jeden Baum single-owner:

      * Genau EIN Dokument-Eigentümer im Teilbaum -> ganzer Baum (alle Knoten) an
        ihn (das behebt auch abweichende Knoten-Owner wie den Admin-Unterordner).
      * MEHRERE Dokument-Eigentümer -> ganzer Baum ownerlos (Admin-Triage).
      * KEIN Dokument:
          - alle Knoten-Owner gleich (0 oder 1 distinct) -> unverändert lassen
            (legitime, konsistente Ordner inkl. leerer Nutzerordner).
          - mehrere Knoten-Owner (strukturell gemischt) -> ganzer Baum ownerlos.

    Idempotent (Single-Pass konvergent) und PVC-/WORM-neutral. reverse = No-op.
    """
    DocumentFolder = apps.get_model("documents", "DocumentFolder")
    Document = apps.get_model("documents", "Document")

    folders = list(
        DocumentFolder.objects.all().values("id", "parent_id", "owner_id", "name")
    )
    by_id = {f["id"]: f for f in folders}
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

    def doc_owners(ids):
        return set(
            Document.objects.filter(folder_id__in=ids, owner__isnull=False)
            .values_list("owner", flat=True)
        )

    def node_owners(ids):
        return {by_id[i]["owner_id"] for i in ids if by_id[i]["owner_id"] is not None}

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

    def set_tree_owner(root_id, ids, owner_id):
        descendants = [i for i in ids if i != root_id]
        if descendants:
            DocumentFolder.objects.filter(id__in=descendants).update(owner_id=owner_id)
        root_obj = DocumentFolder.objects.get(pk=root_id)
        root_obj.owner_id = owner_id
        if owner_id is not None:
            root_obj.name = unique_root_name(owner_id, root_obj.name, root_obj.pk)
        root_obj.save(update_fields=["owner", "name"])

    for root in (f for f in folders if f["parent_id"] is None):
        ids = subtree_ids(root["id"])
        d_owners = doc_owners(ids)
        n_owners = node_owners(ids)

        if len(d_owners) == 1:
            target = next(iter(d_owners))
            if n_owners != {target} or any(
                by_id[i]["owner_id"] != target for i in ids
            ):
                set_tree_owner(root["id"], ids, target)
        elif len(d_owners) >= 2:
            if n_owners:  # irgendein Knoten hat einen Owner -> auf NULL zurücksetzen
                set_tree_owner(root["id"], ids, None)
        else:  # keine Dokumente
            if len(n_owners) >= 2:
                set_tree_owner(root["id"], ids, None)
            # 0/1 distinct Knoten-Owner -> konsistent, unverändert lassen


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0059_repair_folder_owner_consistency"),
    ]

    operations = [
        migrations.RunPython(
            repair_folder_node_owner_consistency, migrations.RunPython.noop
        ),
    ]
