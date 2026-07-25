from django.db import migrations


def repair_folder_null_owner_mix(apps, schema_editor):
    """Idempotente Reparatur – normalisiert auch Bäume mit gemischten NULL-/Owner-
    Knoten.

    0060 entfernte in ``node_owners()`` die ``None``-Owner. Ein Baum mit
    ``root.owner=Alice`` und ``child.owner=NULL`` (z. B. leere Alt-Bestände) erschien
    dadurch als konsistent (ein distinct Knoten-Owner) und blieb unrepariert – der
    NULL-Knoten unter einem Owner erzeugt aber inkonsistente Sichtbarkeit/Rechte.

    Zielzustand pro Wurzelbaum, ``claimants`` = distinct(Dokument-Owner) ∪
    distinct(Nicht-NULL-Knoten-Owner):
      * GENAU EIN claimant -> ALLE Knoten des Baums gehören ihm (NULL-Knoten und
        abweichende Knoten-Owner werden auf diesen einen Owner normalisiert).
      * MEHRERE claimants -> ganzer Baum ownerlos (Admin-Triage).
      * KEIN claimant (nur NULL-Knoten, keine Dokumente) -> unverändert lassen.

    Idempotent (Single-Pass konvergent), PVC-/WORM-neutral. reverse = No-op.
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

    def unique_root_name(owner_id, name, pk):
        # Basisnamen kürzen, damit ``base + " (N)"`` die DB-Grenze (name max_length
        # 255) nicht sprengt (sonst DataError beim Deploy). 245 lässt Platz für das
        # Suffix.
        base, candidate, i = name[:245], name, 1
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
        doc_owners = set(
            Document.objects.filter(folder_id__in=ids, owner__isnull=False)
            .values_list("owner", flat=True)
        )
        node_owners = {by_id[i]["owner_id"] for i in ids if by_id[i]["owner_id"] is not None}
        claimants = doc_owners | node_owners

        if len(claimants) == 1:
            target = next(iter(claimants))
            # Nur schreiben, wenn NICHT schon alle Knoten diesem Owner gehören.
            if any(by_id[i]["owner_id"] != target for i in ids):
                set_tree_owner(root["id"], ids, target)
        elif len(claimants) >= 2:
            if node_owners:  # irgendein Knoten hat einen Owner -> auf NULL
                set_tree_owner(root["id"], ids, None)
        # 0 claimants (nur NULL-Knoten, keine Dokumente) -> unverändert


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0061_documentversion_archive_sha256"),
    ]

    operations = [
        migrations.RunPython(repair_folder_null_owner_mix, migrations.RunPython.noop),
    ]
