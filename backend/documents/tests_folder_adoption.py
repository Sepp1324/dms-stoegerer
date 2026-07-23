"""P1: Migration 0058 adoptiert bestehende owner=NULL-Ordner, damit sie unter dem
neuen Owner-Check wieder zuweisbar sind (Wurzel = Mehrheits-Dokumenteigentümer,
Kinder erben, leere Wurzeln bleiben ownerlos)."""
import hashlib
import importlib

from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from documents.models import Document, DocumentFolder, DocumentVersion

User = get_user_model()


def _adopt():
    mod = importlib.import_module("documents.migrations.0058_adopt_legacy_folders")
    mod.adopt_legacy_folders(global_apps, None)


def _doc(owner, folder, title):
    doc = Document.objects.create(title=title, owner=owner, folder=folder)
    v = DocumentVersion.objects.create(
        document=doc, version_no=1, file_path=f"/tmp/{title}.pdf",
        sha256=hashlib.sha256(title.encode()).hexdigest(), ocr_text="x",
    )
    doc.current_version = v
    doc.save(update_fields=["current_version"])
    return doc


class AdoptLegacyFoldersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("ad_alice", password="pw", role="user")
        cls.bob = User.objects.create_user("ad_bob", password="pw", role="user")

    def test_single_owner_baum_wird_adoptiert(self):
        f = DocumentFolder.objects.create(name="Steuer", owner=None)
        _doc(self.alice, f, "A1")
        _doc(self.alice, f, "A2")
        _adopt()
        f.refresh_from_db()
        self.assertEqual(f.owner_id, self.alice.id)

    def test_verschachtelt_leere_wurzel_wird_ueber_teilbaum_adoptiert(self):
        # Wurzel ist leer, Dokument liegt im Enkel -> Owner muss aus dem GESAMTEN
        # Teilbaum ermittelt werden, sonst bliebe alles owner=NULL.
        root = DocumentFolder.objects.create(name="Akte", owner=None)
        sub = DocumentFolder.objects.create(name="Rechnungen", parent=root, owner=None)
        _doc(self.bob, sub, "R1")  # nur im Unterordner
        _adopt()
        root.refresh_from_db()
        sub.refresh_from_db()
        self.assertEqual(root.owner_id, self.bob.id)   # Wurzel adoptiert
        self.assertEqual(sub.owner_id, self.bob.id)    # Kind adoptiert

    def test_gemischter_baum_bleibt_ownerlos(self):
        # Dokumente mehrerer Eigentümer -> KEINE Mehrheitsentscheidung. Der ganze
        # Baum bleibt ownerlos (Admin-Triage); kein Minderheitsdokument haengt
        # unter einem fremden Eigentuemer.
        root = DocumentFolder.objects.create(name="Gemeinsam", owner=None)
        sub = DocumentFolder.objects.create(name="Sub", parent=root, owner=None)
        _doc(self.alice, root, "A1")
        _doc(self.alice, root, "A2")
        _doc(self.bob, sub, "B1")  # Minderheit
        _adopt()
        root.refresh_from_db()
        sub.refresh_from_db()
        self.assertIsNone(root.owner_id)   # NICHT an alice adoptiert
        self.assertIsNone(sub.owner_id)

    def test_leere_wurzel_bleibt_ownerlos(self):
        f = DocumentFolder.objects.create(name="Leer", owner=None)
        _adopt()
        f.refresh_from_db()
        self.assertIsNone(f.owner_id)  # keine Dokumente -> nicht adoptierbar

    def test_namenskollision_beim_adoptieren(self):
        # Zwei ownerlose Wurzeln "Doppelt", beide mehrheitlich alice -> zweiter wird
        # umbenannt (unique(owner, name) where parent NULL).
        f1 = DocumentFolder.objects.create(name="Doppelt", owner=None)
        f2 = DocumentFolder.objects.create(name="Doppelt", owner=None)
        _doc(self.alice, f1, "X1")
        _doc(self.alice, f2, "X2")
        _adopt()
        f1.refresh_from_db()
        f2.refresh_from_db()
        self.assertEqual(f1.owner_id, self.alice.id)
        self.assertEqual(f2.owner_id, self.alice.id)
        self.assertNotEqual(f1.name, f2.name)  # entzerrt
        self.assertEqual(
            DocumentFolder.objects.filter(owner=self.alice, parent__isnull=True).count(), 2
        )
