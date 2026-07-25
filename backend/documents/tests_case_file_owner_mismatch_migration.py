"""P1: 0063 loest bestehende Owner-Mix-Zuordnungen ueber eine echte Migration.

Die API-Guards verhindern nur NEUE Fremdzuordnungen. Diese Migration bereinigt
Altbestand: ``Document.case_file`` und ``ContractRecord.case_file`` werden auf
NULL gesetzt, wenn die Akte einem anderen Eigentuemer gehoert als das
Dokument/der Vertrag. Konsistente Zuordnungen bleiben unveraendert.
"""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ClearCaseFileOwnerMismatchMigrationTests(TransactionTestCase):
    app = "documents"
    migrate_from = "0062_repair_folder_null_owner_mix"
    migrate_to = "0063_clear_case_file_owner_mismatch"

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([(self.app, target)])
        executor.loader.build_graph()
        return executor.loader.project_state([(self.app, target)]).apps

    def tearDown(self):
        from django.core.management import call_command

        call_command("migrate", self.app, verbosity=0)

    def test_upgrade_loest_fremde_akten_und_behaelt_eigene(self):
        old = self._migrate(self.migrate_from)
        User = old.get_model("accounts", "User")
        CaseFile = old.get_model("documents", "CaseFile")
        Document = old.get_model("documents", "Document")
        ContractRecord = old.get_model("documents", "ContractRecord")

        alice = User.objects.create(username="mm_alice", role="user")
        bob = User.objects.create(username="mm_bob", role="user")

        alice_case = CaseFile.objects.create(title="Alice", owner_id=alice.id)
        bob_case = CaseFile.objects.create(title="Bob", owner_id=bob.id)

        # 1) Dokument von alice, faelschlich an bobs Akte gehaengt -> muss geloest werden.
        doc_mismatch = Document.objects.create(
            title="D-Mismatch", owner_id=alice.id, case_file_id=bob_case.id
        )
        # 2) Dokument von alice in alices Akte -> bleibt.
        doc_ok = Document.objects.create(
            title="D-OK", owner_id=alice.id, case_file_id=alice_case.id
        )
        # 3) Dokument ohne Owner, aber an einer nutzergehoerten Akte -> Mismatch.
        doc_null_owner = Document.objects.create(
            title="D-Null", owner=None, case_file_id=alice_case.id
        )

        # Vertraege: Owner folgt dem Dokument (OneToOne).
        contract_mismatch = ContractRecord.objects.create(
            document_id=doc_ok.id, case_file_id=bob_case.id
        )
        doc_for_ok_contract = Document.objects.create(
            title="D-Contract-OK", owner_id=bob.id, case_file_id=bob_case.id
        )
        contract_ok = ContractRecord.objects.create(
            document_id=doc_for_ok_contract.id, case_file_id=bob_case.id
        )

        new = self._migrate(self.migrate_to)
        D = new.get_model("documents", "Document")
        C = new.get_model("documents", "ContractRecord")

        self.assertIsNone(D.objects.get(pk=doc_mismatch.id).case_file_id)
        self.assertEqual(D.objects.get(pk=doc_ok.id).case_file_id, alice_case.id)
        self.assertIsNone(D.objects.get(pk=doc_null_owner.id).case_file_id)
        self.assertIsNone(C.objects.get(pk=contract_mismatch.id).case_file_id)
        self.assertEqual(C.objects.get(pk=contract_ok.id).case_file_id, bob_case.id)

    def test_idempotent_zweiter_lauf_aendert_nichts(self):
        old = self._migrate(self.migrate_from)
        User = old.get_model("accounts", "User")
        CaseFile = old.get_model("documents", "CaseFile")
        Document = old.get_model("documents", "Document")

        alice = User.objects.create(username="mm_alice2", role="user")
        alice_case = CaseFile.objects.create(title="Alice", owner_id=alice.id)
        doc_ok = Document.objects.create(
            title="D-OK", owner_id=alice.id, case_file_id=alice_case.id
        )

        self._migrate(self.migrate_to)
        # Zweiter Lauf (ueber tearDown/erneutes Anwenden waere No-op) darf die
        # konsistente Zuordnung nicht anfassen.
        D = self._migrate(self.migrate_to).get_model("documents", "Document")
        self.assertEqual(D.objects.get(pk=doc_ok.id).case_file_id, alice_case.id)
