"""P1: 0059 repariert Ordner-Eigentümer über eine echte Migration (MigrationExecutor).

Anders als ein direkter Funktionsaufruf simuliert dieser Test den Upgrade-Pfad:
Daten werden im Zustand aufgebaut, den die FRÜHE 0058 (Mehrheits-/Root-only-
Adoption) hinterlassen hätte, dann wird auf 0059 migriert und das Ergebnis geprüft.
"""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class Repair0059MigrationTests(TransactionTestCase):
    app = "documents"
    migrate_from = "0058_adopt_legacy_folders"
    migrate_to = "0059_repair_folder_owner_consistency"

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([(self.app, target)])
        executor.loader.build_graph()
        return executor.loader.project_state([(self.app, target)]).apps

    def tearDown(self):
        # DB nach dem Test wieder auf den aktuellen Stand bringen (0059 ist Leaf).
        self._migrate(self.migrate_to)

    def test_upgrade_repariert_inkonsistente_und_verschachtelte_baeume(self):
        old = self._migrate(self.migrate_from)
        User = old.get_model("accounts", "User")
        Folder = old.get_model("documents", "DocumentFolder")
        Document = old.get_model("documents", "Document")

        alice = User.objects.create(username="rep_alice", role="user")
        bob = User.objects.create(username="rep_bob", role="user")

        # 1) Gemischter Baum, den die alte 0058 per MEHRHEIT an alice adoptiert hätte
        #    (enthält aber auch ein Dokument von bob) -> inkonsistent.
        mixed = Folder.objects.create(name="Gemeinsam", owner_id=alice.id)
        Document.objects.create(title="A1", owner_id=alice.id, folder_id=mixed.id)
        Document.objects.create(title="A2", owner_id=alice.id, folder_id=mixed.id)
        Document.objects.create(title="B1", owner_id=bob.id, folder_id=mixed.id)

        # 2) Verschachtelter Baum, den die alte (root-only) 0058 NICHT adoptierte
        #    (Wurzel leer, Dokument nur im Unterordner).
        root = Folder.objects.create(name="Akte", owner=None)
        sub = Folder.objects.create(name="Rechnungen", parent_id=root.id, owner=None)
        Document.objects.create(title="C1", owner_id=bob.id, folder_id=sub.id)

        # 3) Legitimer, leerer Nutzerordner -> muss UNVERÄNDERT bleiben.
        legit = Folder.objects.create(name="Privat", owner_id=alice.id)

        new = self._migrate(self.migrate_to)
        F = new.get_model("documents", "DocumentFolder")

        self.assertIsNone(F.objects.get(pk=mixed.id).owner_id)       # gemischt -> ownerlos
        self.assertEqual(F.objects.get(pk=root.id).owner_id, bob.id)  # verschachtelt adoptiert
        self.assertEqual(F.objects.get(pk=sub.id).owner_id, bob.id)
        self.assertEqual(F.objects.get(pk=legit.id).owner_id, alice.id)  # unverändert

    def test_idempotent_zweiter_lauf_aendert_nichts(self):
        old = self._migrate(self.migrate_from)
        User = old.get_model("accounts", "User")
        Folder = old.get_model("documents", "DocumentFolder")
        Document = old.get_model("documents", "Document")
        bob = User.objects.create(username="rep_bob2", role="user")
        root = Folder.objects.create(name="AkteX", owner=None)
        Document.objects.create(title="D1", owner_id=bob.id, folder_id=root.id)

        F1 = self._migrate(self.migrate_to).get_model("documents", "DocumentFolder")
        self.assertEqual(F1.objects.get(pk=root.id).owner_id, bob.id)

        # Zweiter Lauf der 0059-Funktion (direkt) darf nichts ändern.
        import importlib

        mod = importlib.import_module(
            "documents.migrations.0059_repair_folder_owner_consistency"
        )
        from django.apps import apps as global_apps

        mod.repair_folder_owner_consistency(global_apps, None)
        from documents.models import DocumentFolder

        self.assertEqual(DocumentFolder.objects.get(pk=root.id).owner_id, bob.id)
