"""P2/P1: Der konfigurierte Ablagepfad (StoragePath.path_template) wirkt physisch,
bleibt aber ausbruch-sicher (immer unter ARCHIVE_DIR, kein ``..``/absolut) und
race-frei (atomare Zielreservierung)."""
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase

from documents import pipeline, storage
from documents.models import Correspondent, Document, DocumentVersion, StoragePath
from documents.serializers import StoragePathSerializer


class StoragePathArchivePlacementTests(TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.archive_dir = self.root / "archive"
        self.originals = self.root / "originals"
        self.originals.mkdir(parents=True)
        self.archive_dir.mkdir(parents=True)
        self._p = mock.patch.object(storage, "ARCHIVE_DIR", self.archive_dir)
        self._p.start()
        self.addCleanup(self._p.stop)

    def _version(self, *, immutable=False, storage_template=None, correspondent=None, title="Steuer Rechnung"):
        sp = StoragePath.objects.create(name="R", path_template=storage_template) if storage_template else None
        corr = Correspondent.objects.create(name=correspondent) if correspondent else None
        doc = Document.objects.create(title=title, storage_path=sp, correspondent=corr)
        archive = self.originals / "abc.ocr.pdf"
        archive.write_bytes(b"%PDF archive")
        v = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=str(self.originals / "abc.pdf"),
            sha256="a" * 64, archive_path=str(archive), is_immutable=immutable,
        )
        doc.current_version = v
        doc.save(update_fields=["current_version"])
        return v, archive

    def _under_archive(self, path) -> bool:
        return str(Path(path).resolve()).startswith(str(self.archive_dir.resolve()) + os.sep)

    def test_archiv_landet_unter_archive_mit_template(self):
        v, old = self._version(
            storage_template="archive/{jahr}/{korrespondent}/{titel}", correspondent="Stadtwerke"
        )
        pipeline._place_archive_at_storage_path(v)
        v.refresh_from_db()
        self.assertTrue(self._under_archive(v.archive_path))   # im gesicherten Subtree
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(v.archive_path))
        self.assertIn("stadtwerke", v.archive_path)
        self.assertNotIn("archive/archive", v.archive_path)    # kein doppeltes archive/

    def test_absoluter_pfad_wird_eingefangen(self):
        v, _ = self._version(storage_template="/tmp/evil/{titel}")
        pipeline._place_archive_at_storage_path(v)
        v.refresh_from_db()
        # Die absolute Wurzel wird eingefangen: der Pfad liegt UNTER ARCHIVE_DIR
        # (die Segmente "tmp"/"evil" bleiben als harmlose Unterordnernamen erhalten,
        # zeigen aber NICHT mehr auf das echte /tmp/evil).
        self.assertTrue(self._under_archive(v.archive_path))
        self.assertFalse(v.archive_path.startswith("/tmp/evil"))

    def test_dotdot_ausbruch_wird_eingefangen(self):
        v, _ = self._version(storage_template="../../etc/{titel}")
        pipeline._place_archive_at_storage_path(v)
        v.refresh_from_db()
        self.assertTrue(self._under_archive(v.archive_path))

    def test_reservierung_verhindert_ueberschreiben(self):
        # Zwei gleichnamige Dokumente -> zwei UNTERSCHIEDLICHE, real reservierte Ziele.
        doc = Document.objects.create(title="Gleich")
        t1 = storage.build_archive_path(doc)
        t2 = storage.build_archive_path(doc)
        self.assertNotEqual(t1, t2)
        self.assertTrue(os.path.exists(t1) and os.path.exists(t2))  # beide reserviert

    def test_immutable_version_wird_nicht_verschoben(self):
        v, old = self._version(immutable=True, storage_template="archive/{titel}")
        pipeline._place_archive_at_storage_path(v)
        v.refresh_from_db()
        self.assertEqual(v.archive_path, str(old))
        self.assertTrue(os.path.exists(old))

    def test_erfolgreicher_move_loescht_original(self):
        v, old = self._version(storage_template="archive/{titel}")
        pipeline._place_archive_at_storage_path(v)
        v.refresh_from_db()
        self.assertFalse(os.path.exists(old))          # Original entfernt
        self.assertTrue(os.path.exists(v.archive_path))
        with open(v.archive_path, "rb") as fh:
            self.assertEqual(fh.read(), b"%PDF archive")  # Inhalt 1:1 kopiert

    def test_crash_zwischen_kopie_und_commit_haelt_db_konsistent(self):
        # Stirbt der Worker NACH der Kopie, aber VOR version.save(), muss der
        # DB-Zeiger weiter auf das (noch existierende) Original zeigen – nicht auf
        # eine Datei, die es nicht gibt. Sonst versiegelt der Watchdog mit totem
        # archive_path.
        v, old = self._version(storage_template="archive/{titel}")
        real_save = DocumentVersion.save

        def _boom(self_v, *a, **kw):
            if "archive_path" in (kw.get("update_fields") or []):
                raise RuntimeError("worker crash")
            return real_save(self_v, *a, **kw)

        with mock.patch.object(DocumentVersion, "save", _boom):
            pipeline._place_archive_at_storage_path(v)  # best-effort: schluckt Fehler

        v.refresh_from_db()
        self.assertEqual(v.archive_path, str(old))     # DB unverändert
        self.assertTrue(os.path.exists(old))           # Original NICHT gelöscht


class StoragePathTemplateValidationTests(TestCase):
    def _valid(self, template):
        return StoragePathSerializer(data={"name": "X", "path_template": template}).is_valid()

    def test_absolut_abgelehnt(self):
        self.assertFalse(self._valid("/tmp/x/{titel}"))

    def test_dotdot_abgelehnt(self):
        self.assertFalse(self._valid("../{titel}"))

    def test_backslash_abgelehnt(self):
        self.assertFalse(self._valid("a\\b/{titel}"))

    def test_gueltiges_template_ok(self):
        self.assertTrue(self._valid("archive/{jahr}/{korrespondent}/{titel}"))
