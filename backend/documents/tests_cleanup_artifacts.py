"""P1/P2: cleanup_artifact_files entfernt Pfade sicher (Referenz-/Root-Prüfung)
und endet nach erschöpften Retries als FEHLER (statt still als Erfolg)."""
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import storage, tasks
from .models import ArtifactCleanupJob, Document, DocumentVersion

User = get_user_model()


class CleanupArtifactSafetyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cl_user", password="pw", role="user")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _patch_data_dir(self):
        return mock.patch.object(storage, "DATA_DIR", self.root)

    def test_entfernt_unreferenzierte_datei_unter_data_dir(self):
        f = self.root / "orig.pdf"
        f.write_bytes(b"x")
        with self._patch_data_dir():
            res = tasks.cleanup_artifact_files([str(f)])
        self.assertFalse(f.exists())
        self.assertEqual(res["removed"], 1)

    def test_noch_referenzierte_datei_bleibt(self):
        f = self.root / "shared.pdf"
        f.write_bytes(b"x")
        doc = Document.objects.create(title="D", owner=self.user)
        DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=str(f), sha256="a" * 64
        )
        with self._patch_data_dir():
            tasks.cleanup_artifact_files([str(f)])
        self.assertTrue(f.exists())  # noch referenziert -> nicht entfernt

    def test_nicht_kanonische_referenz_schuetzt_datei(self):
        # P1: Eine Version referenziert die gemeinsame Datei über eine NICHT-
        # kanonische Schreibweise (a/../a.pdf). Der Cleanup-Kandidat ist der
        # kanonische Pfad. Ein reiner String-Vergleich uebersaehe die Referenz und
        # loeschte die geteilte Datei -> muss KANONISCH erkannt werden.
        sub = self.root / "originals"
        sub.mkdir()
        f = sub / "a.pdf"
        f.write_bytes(b"x")
        noncanonical = str(sub / ".." / "originals" / "a.pdf")
        self.assertNotEqual(noncanonical, str(f))  # wirklich nicht-kanonisch

        doc = Document.objects.create(title="Shared", owner=self.user)
        DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=noncanonical, sha256="a" * 64
        )
        with self._patch_data_dir():
            res = tasks.cleanup_artifact_files([str(f)])  # kanonischer Kandidat
        self.assertTrue(f.exists())  # trotz abweichender Schreibweise geschuetzt
        self.assertEqual(res["removed"], 0)

    def test_pfad_ausserhalb_data_dir_wird_abgelehnt(self):
        outside = tempfile.NamedTemporaryFile(delete=False)
        outside.write(b"x")
        outside.close()
        outside_path = Path(outside.name)
        self.addCleanup(lambda: outside_path.exists() and outside_path.unlink())
        # Sicherstellen, dass die Datei NICHT unter self.root liegt.
        self.assertFalse(str(outside_path).startswith(str(self.root)))

        with self._patch_data_dir():
            res = tasks.cleanup_artifact_files([str(outside_path)])
        self.assertTrue(outside_path.exists())  # ausserhalb -> nicht angefasst
        self.assertEqual(res["removed"], 0)

    def test_fehlgeschlagen_nach_retries_wirft(self):
        # os.remove auf ein VERZEICHNIS scheitert (OSError). Bei erschoepften
        # Retries endet der Task als FEHLER (Betriebsalarm), nicht als Erfolg.
        d = self.root / "einverzeichnis"
        d.mkdir()
        with self._patch_data_dir():
            result = tasks.cleanup_artifact_files.apply(
                args=[[str(d)]],
                retries=tasks.cleanup_artifact_files.max_retries,
            )
        self.assertTrue(result.failed())

    def test_symlink_mit_anderem_namen_schuetzt_datei(self):
        # P1: Eine Version referenziert die gemeinsame Datei über einen ANDERS
        # BENANNTEN Symlink. Der frühere Basename-Vorfilter übersah das und hätte
        # das Ziel gelöscht -> jetzt kanonisch (realpath) erkannt.
        target = self.root / "original.pdf"
        target.write_bytes(b"x")
        link = self.root / "verweis.pdf"
        os.symlink(target, link)  # anderer Name, gleiches Ziel

        doc = Document.objects.create(title="Sym", owner=self.user)
        DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=str(link), sha256="a" * 64
        )
        with self._patch_data_dir():
            res = tasks.cleanup_artifact_files([str(target)])  # Kandidat = Ziel
        self.assertTrue(target.exists())  # über den Symlink noch referenziert
        self.assertEqual(res["removed"], 0)

    def test_outbox_wird_bei_erfolg_geloescht(self):
        f = self.root / "orig.pdf"
        f.write_bytes(b"x")
        job = ArtifactCleanupJob.objects.create(paths=[str(f)])
        with self._patch_data_dir():
            tasks.cleanup_artifact_files([str(f)], job_id=job.id)
        self.assertFalse(f.exists())
        self.assertFalse(ArtifactCleanupJob.objects.filter(pk=job.id).exists())

    def test_outbox_bleibt_bei_endgueltigem_fehler(self):
        # Endgültig fehlgeschlagene FS-Retries: der Outbox-Job bleibt bestehen
        # (Sweeper übernimmt), statt still verloren zu gehen.
        d = self.root / "dir"
        d.mkdir()
        job = ArtifactCleanupJob.objects.create(paths=[str(d)])
        with self._patch_data_dir():
            result = tasks.cleanup_artifact_files.apply(
                args=[[str(d)]],
                kwargs={"job_id": job.id},
                retries=tasks.cleanup_artifact_files.max_retries,
            )
        self.assertTrue(result.failed())
        self.assertTrue(ArtifactCleanupJob.objects.filter(pk=job.id).exists())
