"""P2: Backfill von archive_sha256 fuer Bestandsversionen (kontrolliert, WORM-sicher)."""
import hashlib
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from documents.models import Document, DocumentVersion


class BackfillArchiveSha256Tests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _version(self, *, content=b"%PDF alt-archiv", write=True, sha=""):
        apath = self.tmp / "arch.pdf"
        if write:
            apath.write_bytes(content)
        doc = Document.objects.create(title="D")
        v = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path="/tmp/x.pdf", sha256="a" * 64,
            archive_path=str(apath), archive_sha256=sha, is_immutable=True,
        )
        doc.current_version = v
        doc.save(update_fields=["current_version"])
        return v, content

    def test_setzt_fehlenden_hash(self):
        v, content = self._version()
        call_command("backfill_archive_sha256", stdout=StringIO())
        v.refresh_from_db()
        self.assertEqual(v.archive_sha256, hashlib.sha256(content).hexdigest())

    def test_dry_run_schreibt_nicht(self):
        v, _ = self._version()
        call_command("backfill_archive_sha256", "--dry-run", stdout=StringIO())
        v.refresh_from_db()
        self.assertEqual(v.archive_sha256, "")   # unverändert

    def test_bereits_gesetzten_hash_nicht_anfassen(self):
        v, _ = self._version(sha="b" * 64)
        call_command("backfill_archive_sha256", stdout=StringIO())
        v.refresh_from_db()
        self.assertEqual(v.archive_sha256, "b" * 64)   # nicht überschrieben

    def test_fehlendes_archiv_wird_uebersprungen(self):
        v, _ = self._version(write=False)
        call_command("backfill_archive_sha256", stdout=StringIO())
        v.refresh_from_db()
        self.assertEqual(v.archive_sha256, "")
