"""P2: Dauerhafte Artefakt-Cleanup-Outbox bei Broker-Ausfall.

Kann der Cleanup-Task bei einem Broker-/Redis-Ausfall nicht eingereiht werden,
wird ein ``ArtifactCleanupJob`` persistiert und von ``process_artifact_cleanup_jobs``
später abgearbeitet – so verwaisen Artefakte nicht dauerhaft.
"""
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import storage, tasks
from .models import ArtifactCleanupJob, Document, DocumentVersion

User = get_user_model()


class CleanupOutboxTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("outbox", password="pw", role="user")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_broker_ausfall_schreibt_outbox_eintrag(self):
        doc = Document.objects.create(title="Del", owner=self.user)
        f = self.root / "orig.pdf"
        f.write_bytes(b"x")
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=str(f),
            sha256="a" * 64, mime_type="application/pdf", size=1, is_immutable=False,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])

        # Broker nicht erreichbar -> .delay() wirft. Statt Best-Effort muss ein
        # dauerhafter Outbox-Eintrag entstehen.
        with mock.patch.object(
            tasks.cleanup_artifact_files, "delay", side_effect=RuntimeError("broker down")
        ), self.captureOnCommitCallbacks(execute=True):
            doc.delete()

        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())
        job = ArtifactCleanupJob.objects.get()
        self.assertIn(str(f), job.paths)
        self.assertTrue(f.exists())  # noch nicht entfernt – erst der Sweeper räumt

    def test_outbox_entsteht_atomar_mit_loeschung(self):
        # P2: Die Outbox-Zeile wird IN der Lösch-Transaktion angelegt – nicht erst
        # im on_commit-Callback. Stirbt der Prozess direkt nach dem Commit (hier:
        # on_commit wird NICHT ausgeführt), existiert der Auftrag trotzdem.
        doc = Document.objects.create(title="Atomar", owner=self.user)
        f = self.root / "orig.pdf"
        f.write_bytes(b"x")
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=str(f),
            sha256="b" * 64, mime_type="application/pdf", size=1, is_immutable=False,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])

        with self.captureOnCommitCallbacks(execute=False):  # Dispatch NICHT ausführen
            doc.delete()

        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())
        job = ArtifactCleanupJob.objects.get()  # trotz nicht-dispatchtem on_commit
        self.assertIn(str(f), job.paths)

    def test_sweeper_entfernt_dateien_und_loescht_job(self):
        f = self.root / "a.pdf"
        f.write_bytes(b"x")
        job = ArtifactCleanupJob.objects.create(paths=[str(f)])

        with mock.patch.object(storage, "DATA_DIR", self.root):
            res = tasks.process_artifact_cleanup_jobs()

        self.assertFalse(f.exists())
        self.assertFalse(ArtifactCleanupJob.objects.filter(pk=job.pk).exists())
        self.assertEqual(res["processed"], 1)

    def test_sweeper_behaelt_job_bei_fehler(self):
        job = ArtifactCleanupJob.objects.create(paths=["/x/y.pdf"])

        # Transienter Fehler -> Pfad bleibt offen, Zeile bleibt für den nächsten Lauf.
        with mock.patch.object(
            tasks, "safe_remove_artifacts", return_value=(0, ["/x/y.pdf"])
        ):
            res = tasks.process_artifact_cleanup_jobs()

        job.refresh_from_db()
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.paths, ["/x/y.pdf"])
        self.assertEqual(res["remaining"], 1)
