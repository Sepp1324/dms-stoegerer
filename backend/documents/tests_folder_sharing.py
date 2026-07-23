"""Tests für die ordnerweite Familien-Freigabe (inkl. Vererbung auf Unterordner)."""
import hashlib

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from accounts.models import Household
from documents.models import Document, DocumentFolder, DocumentVersion

User = get_user_model()


def _doc(owner, title, *, folder=None):
    doc = Document.objects.create(title=title, owner=owner, folder=folder)
    version = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path=f"/tmp/{title}.pdf",
        sha256=hashlib.sha256(title.encode()).hexdigest(),
        ocr_text="Inhalt",
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    return doc


class FolderSharingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("f_alice", password="pw", role="user")
        cls.bob = User.objects.create_user("f_bob", password="pw", role="user")
        cls.carol = User.objects.create_user("f_carol", password="pw", role="user")
        household = Household.objects.create(name="Fam", created_by=cls.alice)
        household.members.add(cls.alice, cls.bob)  # carol NICHT

        # Ordner gehören Alice (Sicherheits-Anker: nur der Owner teilt seine Docs).
        cls.shared_folder = DocumentFolder.objects.create(
            name="Familie", shared_with_household=True, owner=cls.alice
        )
        cls.sub_folder = DocumentFolder.objects.create(
            name="Unterordner", parent=cls.shared_folder, owner=cls.alice
        )  # erbt Freigabe
        cls.private_folder = DocumentFolder.objects.create(name="Privat", owner=cls.alice)

        cls.in_shared = _doc(cls.alice, "InShared", folder=cls.shared_folder)
        cls.in_sub = _doc(cls.alice, "InSub", folder=cls.sub_folder)
        cls.in_private = _doc(cls.alice, "InPrivate", folder=cls.private_folder)
        cls.no_folder = _doc(cls.alice, "NoFolder", folder=None)

    def _list_ids(self):
        return [d["id"] for d in self.client.get("/api/documents/").data["results"]]

    def test_member_sees_shared_folder_and_subfolder(self):
        self.client.force_authenticate(self.bob)
        ids = self._list_ids()
        self.assertIn(self.in_shared.id, ids)
        self.assertIn(self.in_sub.id, ids)  # Vererbung auf Unterordner
        self.assertNotIn(self.in_private.id, ids)
        self.assertNotIn(self.no_folder.id, ids)

    def test_member_reads_but_cannot_write(self):
        self.client.force_authenticate(self.bob)
        self.assertEqual(
            self.client.get(f"/api/documents/{self.in_sub.id}/").status_code, 200
        )
        self.assertEqual(
            self.client.patch(
                f"/api/documents/{self.in_sub.id}/", {"title": "x"}, format="json"
            ).status_code,
            404,
        )

    def test_nonmember_sees_nothing_shared(self):
        self.client.force_authenticate(self.carol)
        ids = self._list_ids()
        self.assertNotIn(self.in_shared.id, ids)
        self.assertNotIn(self.in_sub.id, ids)

    def test_toggle_folder_share_reveals_docs(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.patch(
            f"/api/folders/{self.private_folder.id}/",
            {"shared_with_household": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.private_folder.refresh_from_db()
        self.assertTrue(self.private_folder.shared_with_household)

        self.client.force_authenticate(self.bob)
        self.assertIn(self.in_private.id, self._list_ids())


class FolderShareOwnershipTests(APITestCase):
    """P1: Ordnerfreigabe ist owner-verankert – ein Mitglied kann weder fremde
    Ordner freigeben noch über einen eigenen Ordner fremde Dokumente exponieren."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("o_alice", password="pw", role="user")
        cls.bob = User.objects.create_user("o_bob", password="pw", role="user")
        hh = Household.objects.create(name="Fam", created_by=cls.alice)
        hh.members.add(cls.alice, cls.bob)

    def _list_ids(self, user):
        self.client.force_authenticate(user)
        return [d["id"] for d in self.client.get("/api/documents/").data["results"]]

    def test_fremder_ordner_freigabe_wird_abgelehnt(self):
        folder = DocumentFolder.objects.create(name="AlicesOrdner", owner=self.alice)
        self.client.force_authenticate(self.bob)  # NICHT der Owner
        resp = self.client.patch(
            f"/api/folders/{folder.id}/", {"shared_with_household": True}, format="json"
        )
        self.assertEqual(resp.status_code, 403)
        folder.refresh_from_db()
        self.assertFalse(folder.shared_with_household)

    def test_ownerloser_ordner_nur_admin(self):
        folder = DocumentFolder.objects.create(name="Global", owner=None)
        self.client.force_authenticate(self.bob)
        resp = self.client.patch(
            f"/api/folders/{folder.id}/", {"shared_with_household": True}, format="json"
        )
        self.assertEqual(resp.status_code, 403)

    def test_ordnerfreigabe_exponiert_nur_owner_dokumente(self):
        # Bob besitzt und teilt einen Ordner; Alice hat ein privates Dokument darin
        # abgelegt. Bobs Freigabe darf ALICES Dokument NICHT exponieren.
        folder = DocumentFolder.objects.create(
            name="BobsOrdner", owner=self.bob, shared_with_household=True
        )
        bob_doc = _doc(self.bob, "BobEigen", folder=folder)
        alice_doc = _doc(self.alice, "AliceFremd", folder=folder)

        alice_sees = self._list_ids(self.alice)
        self.assertIn(bob_doc.id, alice_sees)        # Bobs eigenes Doc: geteilt
        self.assertIn(alice_doc.id, alice_sees)      # Alices eigenes Doc: sowieso sichtbar

        # Umgekehrt: für Bob ist Alices Fremd-Doc im selben Ordner NICHT sichtbar
        # (die Ordnerfreigabe wirkt nur für Bobs eigene Dokumente).
        bob_sees = self._list_ids(self.bob)
        self.assertIn(bob_doc.id, bob_sees)
        self.assertNotIn(alice_doc.id, bob_sees)

    # --- Mutation/Löschung nur durch Owner/Admin (P1) -------------------------
    def test_fremden_ordner_umbenennen_abgelehnt(self):
        folder = DocumentFolder.objects.create(name="Alices", owner=self.alice)
        self.client.force_authenticate(self.bob)
        resp = self.client.patch(
            f"/api/folders/{folder.id}/", {"name": "Gekapert"}, format="json"
        )
        self.assertEqual(resp.status_code, 403)
        folder.refresh_from_db()
        self.assertEqual(folder.name, "Alices")

    def test_fremden_ordner_verschieben_abgelehnt(self):
        parent = DocumentFolder.objects.create(name="BobParent", owner=self.bob)
        folder = DocumentFolder.objects.create(name="Alices", owner=self.alice)
        self.client.force_authenticate(self.bob)
        resp = self.client.patch(
            f"/api/folders/{folder.id}/", {"parent": parent.id}, format="json"
        )
        self.assertEqual(resp.status_code, 403)
        folder.refresh_from_db()
        self.assertIsNone(folder.parent_id)

    def test_fremden_ordner_loeschen_abgelehnt(self):
        folder = DocumentFolder.objects.create(name="Alices", owner=self.alice)
        self.client.force_authenticate(self.bob)
        resp = self.client.delete(f"/api/folders/{folder.id}/")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(DocumentFolder.objects.filter(pk=folder.id).exists())

    def test_ownerlosen_ordner_loeschen_nur_admin(self):
        folder = DocumentFolder.objects.create(name="Global", owner=None)
        self.client.force_authenticate(self.bob)
        self.assertEqual(self.client.delete(f"/api/folders/{folder.id}/").status_code, 403)

    def test_owner_darf_eigenen_ordner_umbenennen_und_loeschen(self):
        folder = DocumentFolder.objects.create(name="Meiner", owner=self.bob)
        self.client.force_authenticate(self.bob)
        self.assertEqual(
            self.client.patch(
                f"/api/folders/{folder.id}/", {"name": "Umbenannt"}, format="json"
            ).status_code,
            200,
        )
        self.assertEqual(self.client.delete(f"/api/folders/{folder.id}/").status_code, 204)

    def test_admin_darf_fremden_und_globalen_ordner_verwalten(self):
        admin = User.objects.create_user("o_admin", password="pw", role="admin")
        alices = DocumentFolder.objects.create(name="Alices", owner=self.alice)
        glob = DocumentFolder.objects.create(name="Global", owner=None)
        self.client.force_authenticate(admin)
        self.assertEqual(
            self.client.patch(
                f"/api/folders/{alices.id}/", {"name": "AdminEdit"}, format="json"
            ).status_code,
            200,
        )
        self.assertEqual(self.client.delete(f"/api/folders/{glob.id}/").status_code, 204)


class FolderTreeOwnerConsistencyTests(APITestCase):
    """P2: Kein gemischter Eigentümerbaum – Unterordner nur unter eigenem Parent,
    Root-Namen pro Owner eindeutig (kein Blockieren fremder Namen, kein CASCADE
    über Owner-Grenzen)."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("t_alice", password="pw", role="user")
        cls.bob = User.objects.create_user("t_bob", password="pw", role="user")
        cls.admin = User.objects.create_user("t_admin", password="pw", role="admin")

    def test_anlegen_unter_fremdem_parent_abgelehnt(self):
        alices = DocumentFolder.objects.create(name="AlicesRoot", owner=self.alice)
        self.client.force_authenticate(self.bob)
        resp = self.client.post(
            "/api/folders/", {"name": "BobsKind", "parent": alices.id}, format="json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("parent", resp.data)
        self.assertFalse(DocumentFolder.objects.filter(name="BobsKind").exists())

    def test_eigenes_kind_unter_fremden_parent_verschieben_abgelehnt(self):
        alices = DocumentFolder.objects.create(name="AlicesRoot2", owner=self.alice)
        bobs = DocumentFolder.objects.create(name="BobsRoot", owner=self.bob)
        self.client.force_authenticate(self.bob)
        resp = self.client.patch(
            f"/api/folders/{bobs.id}/", {"parent": alices.id}, format="json"
        )
        self.assertEqual(resp.status_code, 400)
        bobs.refresh_from_db()
        self.assertIsNone(bobs.parent_id)  # nicht in fremden Baum gehängt

    def test_anlegen_unter_eigenem_parent_ok(self):
        self.client.force_authenticate(self.bob)
        parent = self.client.post("/api/folders/", {"name": "BobsParent"}, format="json")
        self.assertEqual(parent.status_code, 201)
        child = self.client.post(
            "/api/folders/",
            {"name": "BobsChild", "parent": parent.data["id"]},
            format="json",
        )
        self.assertEqual(child.status_code, 201)

    def test_gleicher_root_name_pro_owner_erlaubt(self):
        # Alice legt "Steuer" an; Bob darf denselben Root-Namen verwenden.
        self.client.force_authenticate(self.alice)
        self.assertEqual(
            self.client.post("/api/folders/", {"name": "Steuer"}, format="json").status_code,
            201,
        )
        self.client.force_authenticate(self.bob)
        self.assertEqual(
            self.client.post("/api/folders/", {"name": "Steuer"}, format="json").status_code,
            201,
        )
        self.assertEqual(DocumentFolder.objects.filter(name="Steuer").count(), 2)

    def test_gleicher_root_name_selber_owner_abgelehnt(self):
        self.client.force_authenticate(self.alice)
        self.assertEqual(
            self.client.post("/api/folders/", {"name": "Doppelt"}, format="json").status_code,
            201,
        )
        self.assertEqual(
            self.client.post("/api/folders/", {"name": "Doppelt"}, format="json").status_code,
            400,
        )

    def test_admin_darf_keinen_gemischten_baum_erzeugen(self):
        # Auch Admins duerfen keinen owner-uebergreifenden Baum bauen (der Ordner
        # gehoert dem Admin, der Parent Alice -> gemischt -> abgelehnt).
        alices = DocumentFolder.objects.create(name="AlicesAdminRoot", owner=self.alice)
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            "/api/folders/", {"name": "AdminKind", "parent": alices.id}, format="json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("parent", resp.data)

    def test_admin_darf_unter_eigenem_parent_einordnen(self):
        parent = DocumentFolder.objects.create(name="AdminRoot", owner=self.admin)
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            "/api/folders/", {"name": "AdminKind", "parent": parent.id}, format="json"
        )
        self.assertEqual(resp.status_code, 201)


class RepairMixedOwnerTreesMigrationTests(TestCase):
    """P1: Die Datenmigration 0058 trennt bereits vorhandene owner-übergreifende
    Parent/Child-Kanten (die alte API erlaubte sie)."""

    def _repair(self):
        import importlib

        from django.apps import apps as global_apps

        mod = importlib.import_module(
            "documents.migrations.0058_repair_mixed_owner_folder_trees"
        )
        mod.repair_mixed_owner_trees(global_apps, None)

    def test_fremdes_kind_wird_zum_root_getrennt(self):
        alice = User.objects.create_user("m_alice", password="pw", role="user")
        bob = User.objects.create_user("m_bob", password="pw", role="user")
        bobs_root = DocumentFolder.objects.create(name="BobRoot", owner=bob)
        alices_child = DocumentFolder.objects.create(
            name="AliceKind", parent=bobs_root, owner=alice
        )

        self._repair()

        alices_child.refresh_from_db()
        self.assertIsNone(alices_child.parent_id)          # getrennt -> Root
        self.assertTrue(DocumentFolder.objects.filter(pk=alices_child.pk).exists())
        # Der eigene Teilbaum-Owner (bob) ist unberührt
        self.assertTrue(DocumentFolder.objects.filter(pk=bobs_root.pk).exists())

    def test_gleiche_owner_kante_bleibt(self):
        alice = User.objects.create_user("m_alice2", password="pw", role="user")
        root = DocumentFolder.objects.create(name="R", owner=alice)
        child = DocumentFolder.objects.create(name="C", parent=root, owner=alice)

        self._repair()

        child.refresh_from_db()
        self.assertEqual(child.parent_id, root.pk)         # unverändert

    def test_namenskollision_beim_trennen_wird_aufgeloest(self):
        alice = User.objects.create_user("m_alice3", password="pw", role="user")
        bob = User.objects.create_user("m_bob3", password="pw", role="user")
        DocumentFolder.objects.create(name="X", owner=alice)               # bestehender Root
        bobs_root = DocumentFolder.objects.create(name="BobRoot3", owner=bob)
        collided = DocumentFolder.objects.create(name="X", parent=bobs_root, owner=alice)

        self._repair()

        collided.refresh_from_db()
        self.assertIsNone(collided.parent_id)
        self.assertNotEqual(collided.name, "X")            # umbenannt bei Kollision
        self.assertEqual(
            DocumentFolder.objects.filter(owner=alice, parent__isnull=True).count(), 2
        )
