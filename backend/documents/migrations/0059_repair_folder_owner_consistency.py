from django.db import migrations


def repair_folder_owner_consistency(apps, schema_editor):
    """Idempotente Reparatur der Ordner-Eigentümer – unabhängig davon, welche
    Fassung von 0058 ein Cluster bereits ausgeführt hat.

    Hintergrund: 0058 wurde nach dem ersten Ausrollen noch verändert (erst
    Mehrheits-/Root-only-Adoption, später Teilbaum-Logik). Django merkt sich nur,
    DASS 0058 lief – nicht MIT welchem Inhalt. Cluster, die die frühe Fassung
    ausgeführt haben, behalten deren fehlerhaftes Ergebnis. Diese Migration stellt
    den korrekten Zielzustand her und ist idempotent (mehrfach ausführbar, auch auf
    frischen Clustern nach jeder 0058-Fassung).

    Zielzustand pro Wurzelbaum (Wurzel + alle Nachfahren), Owner aus dem GESAMTEN
    Teilbaum ermittelt:
      * Genau EIN Dokumenteigentümer  -> der ganze Baum gehört ihm (adoptieren).
      * Baum GEHÖRT bereits jemandem, enthält aber ein Dokument eines ANDEREN
        Eigentümers (z. B. Mehrheits-Adoption der alten 0058) -> INKONSISTENT:
        ganzen Baum ownerlos machen (Admin-Triage), damit kein Minderheitsdokument
        unter einem fremden Owner hängt.
      * Kein Dokument (bzw. nur Dokumente ohne Owner) -> Baum bleibt/wird ownerlos.

    Wichtig: Legitime, nach dem Owner-Feature angelegte Ordner sind konsistent
    (alle enthaltenen Dokumente gehören dem Ordner-Owner, leere Ordner haben kein
    Fremd-Dokument) -> sie werden NICHT angefasst. Nur inkonsistente Bäume ändern
    sich.

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

    def subtree_owners(ids):
        # Python-``set`` statt ``.distinct()``: eine Default-``Meta.ordering`` auf
        # ``Document`` zöge die Order-Spalte in den SELECT und ``.distinct()`` würde
        # nicht nach owner allein deduplizieren.
        return set(
            Document.objects.filter(folder_id__in=ids, owner__isnull=False)
            .values_list("owner", flat=True)
        )

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

    roots = [f for f in folders if f["parent_id"] is None]
    for root in roots:
        ids = subtree_ids(root["id"])
        owners = subtree_owners(ids)
        root_owner = root["owner_id"]

        if root_owner is None:
            # Ownerloser Baum: adoptieren, wenn genau ein Dokumenteigentümer.
            if len(owners) == 1:
                owner_id = next(iter(owners))
                descendants = [i for i in ids if i != root["id"]]
                if descendants:
                    DocumentFolder.objects.filter(
                        id__in=descendants, owner__isnull=True
                    ).update(owner_id=owner_id)
                root_obj = DocumentFolder.objects.get(pk=root["id"])
                root_obj.owner_id = owner_id
                root_obj.name = unique_root_name(owner_id, root_obj.name, root_obj.pk)
                root_obj.save(update_fields=["owner", "name"])
            # sonst (0 oder >=2 Owner): ownerlos lassen.
        else:
            # Baum gehört bereits jemandem: nur eingreifen, wenn ein FREMDES
            # Dokument enthalten ist (inkonsistent -> ownerlos = Admin-Triage).
            if owners - {root_owner}:
                DocumentFolder.objects.filter(id__in=ids).update(owner_id=None)
            # sonst konsistent -> unverändert (auch legitime leere Nutzerordner).


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0058_adopt_legacy_folders"),
    ]

    operations = [
        migrations.RunPython(
            repair_folder_owner_consistency, migrations.RunPython.noop
        ),
    ]
