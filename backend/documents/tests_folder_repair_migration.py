"""P1: 0059 repariert Ordner-Eigentümer über eine echte Migration (MigrationExecutor).

Anders als ein direkter Funktionsaufruf simuliert dieser Test den Upgrade-Pfad:
Daten werden im Zustand aufgebaut, den die FRÜHE 0058 (Mehrheits-/Root-only-
Adoption) hinterlassen hätte, dann wird auf 0059 migriert und das Ergebnis geprüft.
"""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase


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
        # DB nach dem Test wieder auf den AKTUELLEN Leaf bringen (nicht nur migrate_to)
        # – sonst fehlt nachfolgenden Tests z. B. archive_sha256 (0061+). ``migrate``
        # ohne Ziel wandert dynamisch bis zum jeweils neuesten Leaf.
        from django.core.management import call_command

        call_command("migrate", self.app, verbosity=0)

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


class Repair0060NodeOwnerMigrationTests(TransactionTestCase):
    """P1: 0060 zieht zusätzlich die ORDNERKNOTEN-Owner heran – ein Admin-
    Unterordner unter Alices Root (gemischte Knoten-Owner) wird bereinigt."""

    app = "documents"
    migrate_from = "0059_repair_folder_owner_consistency"
    migrate_to = "0060_repair_folder_node_owner_consistency"

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([(self.app, target)])
        executor.loader.build_graph()
        return executor.loader.project_state([(self.app, target)]).apps

    def tearDown(self):
        # Auf den AKTUELLEN Leaf migrieren (nicht nur migrate_to), damit spätere
        # Tests die volle, aktuelle Struktur sehen.
        from django.core.management import call_command

        call_command("migrate", self.app, verbosity=0)

    def test_admin_unterordner_mit_alice_docs_wird_alice(self):
        old = self._migrate(self.migrate_from)
        User = old.get_model("accounts", "User")
        Folder = old.get_model("documents", "DocumentFolder")
        Document = old.get_model("documents", "Document")
        alice = User.objects.create(username="n_alice", role="user")
        admin = User.objects.create(username="n_admin", role="admin")
        root = Folder.objects.create(name="AliceRoot", owner_id=alice.id)
        sub = Folder.objects.create(name="AdminSub", parent_id=root.id, owner_id=admin.id)
        Document.objects.create(title="A", owner_id=alice.id, folder_id=root.id)

        F = self._migrate(self.migrate_to).get_model("documents", "DocumentFolder")
        self.assertEqual(F.objects.get(pk=root.id).owner_id, alice.id)
        self.assertEqual(F.objects.get(pk=sub.id).owner_id, alice.id)  # admin -> alice

    def test_gemischte_knoten_ohne_docs_wird_ownerlos(self):
        old = self._migrate(self.migrate_from)
        User = old.get_model("accounts", "User")
        Folder = old.get_model("documents", "DocumentFolder")
        alice = User.objects.create(username="n_alice2", role="user")
        admin = User.objects.create(username="n_admin2", role="admin")
        root = Folder.objects.create(name="AliceRoot2", owner_id=alice.id)
        sub = Folder.objects.create(name="AdminSub2", parent_id=root.id, owner_id=admin.id)

        F = self._migrate(self.migrate_to).get_model("documents", "DocumentFolder")
        self.assertIsNone(F.objects.get(pk=root.id).owner_id)   # gemischt -> ownerlos
        self.assertIsNone(F.objects.get(pk=sub.id).owner_id)

    def test_konsistenter_leerer_ordner_unveraendert(self):
        old = self._migrate(self.migrate_from)
        User = old.get_model("accounts", "User")
        Folder = old.get_model("documents", "DocumentFolder")
        alice = User.objects.create(username="n_alice3", role="user")
        legit = Folder.objects.create(name="AlicePrivat", owner_id=alice.id)

        F = self._migrate(self.migrate_to).get_model("documents", "DocumentFolder")
        self.assertEqual(F.objects.get(pk=legit.id).owner_id, alice.id)  # unverändert


class Repair0062NullOwnerMixTests(TransactionTestCase):
    """P2: 0062 normalisiert Baeume mit gemischten NULL-/Owner-Knoten – ein
    NULL-Kind unter Alices Root wird Alice (0060 uebersah das)."""

    app = "documents"
    migrate_from = "0061_documentversion_archive_sha256"
    migrate_to = "0062_repair_folder_null_owner_mix"

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([(self.app, target)])
        executor.loader.build_graph()
        return executor.loader.project_state([(self.app, target)]).apps

    def tearDown(self):
        from django.core.management import call_command

        call_command("migrate", self.app, verbosity=0)

    def test_null_kind_unter_owner_wird_owner(self):
        old = self._migrate(self.migrate_from)
        User = old.get_model("accounts", "User")
        Folder = old.get_model("documents", "DocumentFolder")
        alice = User.objects.create(username="z_alice", role="user")
        root = Folder.objects.create(name="AliceRootZ", owner_id=alice.id)
        sub = Folder.objects.create(name="NullSub", parent_id=root.id, owner=None)

        F = self._migrate(self.migrate_to).get_model("documents", "DocumentFolder")
        self.assertEqual(F.objects.get(pk=root.id).owner_id, alice.id)
        self.assertEqual(F.objects.get(pk=sub.id).owner_id, alice.id)  # NULL -> alice

    def test_reiner_null_baum_bleibt(self):
        old = self._migrate(self.migrate_from)
        Folder = old.get_model("documents", "DocumentFolder")
        root = Folder.objects.create(name="GlobalZ", owner=None)
        sub = Folder.objects.create(name="GlobalSubZ", parent_id=root.id, owner=None)

        F = self._migrate(self.migrate_to).get_model("documents", "DocumentFolder")
        self.assertIsNone(F.objects.get(pk=root.id).owner_id)   # unverändert
        self.assertIsNone(F.objects.get(pk=sub.id).owner_id)


class MigrationLongRootNameTruncationTests(TestCase):
    """P2: Bei einer Namenskollision langer Root-Namen darf das Zählsuffix die
    255-Zeichen-Grenze (DataError) nicht sprengen."""

    def test_0062_kollision_langer_name_kein_dataerror(self):
        import importlib

        from django.apps import apps as global_apps
        from django.contrib.auth import get_user_model
        from documents.models import Document, DocumentFolder

        User = get_user_model()
        alice = User.objects.create_user("lt_alice", password="pw", role="user")
        long_name = "X" * 250
        # Bereits vorhandener Root von alice mit dem langen Namen.
        DocumentFolder.objects.create(name=long_name, owner=alice)
        # Zu adoptierender ownerloser Root mit demselben Namen + alice-Dokument.
        root2 = DocumentFolder.objects.create(name=long_name, owner=None)
        Document.objects.create(title="D", owner=alice, folder=root2)

        mod = importlib.import_module(
            "documents.migrations.0062_repair_folder_null_owner_mix"
        )
        mod.repair_folder_null_owner_mix(global_apps, None)  # darf NICHT DataError werfen

        root2.refresh_from_db()
        self.assertEqual(root2.owner_id, alice.id)     # adoptiert
        self.assertNotEqual(root2.name, long_name)     # entzerrt
        self.assertLessEqual(len(root2.name), 255)     # innerhalb der Grenze
