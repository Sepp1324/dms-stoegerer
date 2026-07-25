"""Bereinigt bestehende Owner-Mix-Verletzungen bei Akten-Zuordnungen (P1).

Die neuen API-Guards (perform_create/perform_update fuer ContractRecord,
add_documents/apply_case_candidate fuer CaseFile) verhindern NEUE Fremdzuordnungen
– aber bereits vorhandene ``Document.case_file``- bzw. ``ContractRecord.case_file``-
Beziehungen mit abweichendem Eigentuemer blieben bestehen und weiter abrufbar
(die Liste filtert nur nach ``document__owner``, ``case_file_title`` wurde weiter
ausgegeben = Datenleck). Diese Datenmigration loest genau diese inkonsistenten
Beziehungen (setzt ``case_file = NULL``); die Dokumente/Vertraege selbst bleiben
unberuehrt.

Owner-Vergleich in Python (nicht via F()/SQL), um NULL-Semantik eindeutig zu
halten: gleiche Owner (inkl. beide NULL) bleiben, jede Abweichung wird geloest.
Nicht umkehrbar (die alte, ungueltige Zuordnung wird bewusst nicht rekonstruiert).
"""
from django.db import migrations


def clear_owner_mismatch(apps, schema_editor):
    Document = apps.get_model("documents", "Document")
    ContractRecord = apps.get_model("documents", "ContractRecord")

    doc_ids = [
        doc.id
        for doc in Document.objects.filter(case_file__isnull=False).select_related(
            "case_file"
        )
        if doc.case_file.owner_id != doc.owner_id
    ]
    if doc_ids:
        Document.objects.filter(id__in=doc_ids).update(case_file=None)

    contract_ids = [
        contract.id
        for contract in ContractRecord.objects.filter(
            case_file__isnull=False
        ).select_related("case_file", "document")
        if contract.case_file.owner_id != contract.document.owner_id
    ]
    if contract_ids:
        ContractRecord.objects.filter(id__in=contract_ids).update(case_file=None)


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0062_repair_folder_null_owner_mix"),
    ]

    operations = [
        migrations.RunPython(clear_owner_mismatch, migrations.RunPython.noop),
    ]
