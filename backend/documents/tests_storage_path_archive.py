"""P2: Der konfigurierte Ablagepfad (StoragePath.path_template) wirkt physisch –
das OCR-Archiv wird vor dem Versiegeln an build_archive_path verschoben."""
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from documents import pipeline, storage
from documents.models import Correspondent, Document, DocumentVersion, StoragePath

User = get_user_model()


class StoragePathArchivePlacementTests(TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp())
        (self.data / "originals").mkdir()
        self.patcher = mock.patch.object(storage, "DATA_DIR", self.data)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _version(self, *, immutable=False, storage_template=None, correspondent=None):
        sp = None
        if storage_template:
            sp = StoragePath.objects.create(name="Rechnungen", path_template=storage_template)
        corr = Correspondent.objects.create(name=correspondent) if correspondent else None
        doc = Document.objects.create(
            title="Steuer Rechnung", storage_path=sp, correspondent=corr
        )
        archive = self.data / "originals" / "abc.ocr.pdf"
        archive.write_bytes(b"%PDF archive")
        v = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=str(self.data / "originals" / "abc.pdf"),
            sha256="a" * 64, archive_path=str(archive), is_immutable=immutable,
        )
        doc.current_version = v
        doc.save(update_fields=["current_version"])
        return v, archive

    def test_archiv_wird_an_template_pfad_verschoben(self):
        v, old = self._version(
            storage_template="archive/{jahr}/{korrespondent}/{titel}",
            correspondent="Stadtwerke",
        )
        pipeline._place_archive_at_storage_path(v)
        v.refresh_from_db()
        self.assertNotEqual(v.archive_path, str(old))
        self.assertFalse(os.path.exists(old))              # alt weg
        self.assertTrue(os.path.exists(v.archive_path))    # neu da
        self.assertIn("stadtwerke", v.archive_path)        # Korrespondent im Pfad
        self.assertTrue(v.archive_path.endswith(".pdf"))
        self.assertFalse(v.archive_path.endswith(".ocr.pdf"))

    def test_idempotent_nach_verschieben(self):
        v, _old = self._version(storage_template="archive/{jahr}/{titel}")
        pipeline._place_archive_at_storage_path(v)
        v.refresh_from_db()
        placed = v.archive_path
        pipeline._place_archive_at_storage_path(v)  # zweiter Lauf: kein .ocr.pdf mehr
        v.refresh_from_db()
        self.assertEqual(v.archive_path, placed)

    def test_immutable_version_wird_nicht_verschoben(self):
        v, old = self._version(immutable=True, storage_template="archive/{titel}")
        pipeline._place_archive_at_storage_path(v)
        v.refresh_from_db()
        self.assertEqual(v.archive_path, str(old))  # WORM: kein Move
        self.assertTrue(os.path.exists(old))

    def test_kein_archiv_kein_fehler(self):
        doc = Document.objects.create(title="Ohne Archiv")
        v = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path="/x/y.pdf", sha256="b" * 64, archive_path=""
        )
        pipeline._place_archive_at_storage_path(v)  # darf nicht werfen
        v.refresh_from_db()
        self.assertEqual(v.archive_path, "")
