from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from documents.models import BackupMonitor, BackupRun, Document, DocumentVersion, OCRStatus


class BackupStatusApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(
            username="admin", password="pw", role="admin"
        )
        cls.user = User.objects.create_user(username="user", password="pw", role="user")

    def test_non_admin_forbidden(self):
        self.client.force_authenticate(self.user)

        resp = self.client.get("/api/system/backup-status/")

        self.assertEqual(resp.status_code, 403)

    @override_settings(BACKUP_ALERT_AFTER_HOURS=36)
    def test_recent_success_is_ok(self):
        now = timezone.now()
        BackupMonitor.objects.create(
            kind=BackupMonitor.Kind.BACKUP,
            status=BackupMonitor.Status.SUCCESS,
            artifact_timestamp="20260706-084501",
            last_success_at=now - timedelta(hours=2),
            last_finished_at=now - timedelta(hours=2),
        )
        BackupMonitor.objects.create(
            kind=BackupMonitor.Kind.RESTORE_DRILL,
            status=BackupMonitor.Status.SUCCESS,
            artifact_timestamp="20260706-084501",
            last_success_at=now - timedelta(hours=1),
            last_finished_at=now - timedelta(hours=1),
        )
        self.client.force_authenticate(self.admin)

        resp = self.client.get("/api/system/backup-status/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "ok")
        self.assertFalse(resp.data["backup"]["stale"])
        self.assertEqual(resp.data["backup"]["artifact_timestamp"], "20260706-084501")
        self.assertEqual(resp.data["cronjob"]["alert_after_hours"], 36)

    @override_settings(BACKUP_ALERT_AFTER_HOURS=36)
    def test_old_backup_warns(self):
        BackupMonitor.objects.create(
            kind=BackupMonitor.Kind.BACKUP,
            status=BackupMonitor.Status.SUCCESS,
            last_success_at=timezone.now() - timedelta(hours=40),
        )
        self.client.force_authenticate(self.admin)

        resp = self.client.get("/api/system/backup-status/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "warn")
        self.assertTrue(resp.data["backup"]["stale"])

    def test_failed_restore_drill_errors(self):
        BackupMonitor.objects.create(
            kind=BackupMonitor.Kind.BACKUP,
            status=BackupMonitor.Status.SUCCESS,
            last_success_at=timezone.now(),
        )
        BackupMonitor.objects.create(
            kind=BackupMonitor.Kind.RESTORE_DRILL,
            status=BackupMonitor.Status.FAILED,
            message="Import fehlgeschlagen",
        )
        self.client.force_authenticate(self.admin)

        resp = self.client.get("/api/system/backup-status/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "error")
        self.assertEqual(resp.data["restore_drill"]["message"], "Import fehlgeschlagen")

    def test_size_and_history_in_response(self):
        BackupMonitor.objects.create(
            kind=BackupMonitor.Kind.BACKUP,
            status=BackupMonitor.Status.SUCCESS,
            last_success_at=timezone.now(),
            size_bytes=1234567,
        )
        BackupRun.objects.create(
            kind=BackupMonitor.Kind.BACKUP,
            status=BackupMonitor.Status.SUCCESS,
            artifact_timestamp="20260706-084501",
            size_bytes=1234567,
        )
        self.client.force_authenticate(self.admin)

        resp = self.client.get("/api/system/backup-status/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["backup"]["size_bytes"], 1234567)
        self.assertIn("history", resp.data)
        history = resp.data["history"][BackupMonitor.Kind.BACKUP]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["size_bytes"], 1234567)


class RecordBackupStatusCommandTests(TestCase):
    def test_success_sets_size_and_creates_history(self):
        call_command(
            "record_backup_status",
            kind=BackupMonitor.Kind.BACKUP,
            status=BackupMonitor.Status.SUCCESS,
            artifact_timestamp="20260706-084501",
            message="ok",
            size_bytes=987654,
        )

        monitor = BackupMonitor.objects.get(kind=BackupMonitor.Kind.BACKUP)
        self.assertEqual(monitor.size_bytes, 987654)

        runs = BackupRun.objects.filter(kind=BackupMonitor.Kind.BACKUP)
        self.assertEqual(runs.count(), 1)
        self.assertEqual(runs.first().size_bytes, 987654)
        self.assertEqual(runs.first().status, BackupMonitor.Status.SUCCESS)

    def test_running_creates_no_history(self):
        call_command(
            "record_backup_status",
            kind=BackupMonitor.Kind.BACKUP,
            status=BackupMonitor.Status.RUNNING,
            message="läuft",
        )

        self.assertEqual(BackupRun.objects.count(), 0)

    def test_failed_creates_history(self):
        call_command(
            "record_backup_status",
            kind=BackupMonitor.Kind.BACKUP,
            status=BackupMonitor.Status.FAILED,
            message="kaputt",
        )

        runs = BackupRun.objects.filter(kind=BackupMonitor.Kind.BACKUP)
        self.assertEqual(runs.count(), 1)
        self.assertEqual(runs.first().status, BackupMonitor.Status.FAILED)


class OCRHealthApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(
            username="ocr_admin", password="pw", role="admin"
        )
        cls.user = User.objects.create_user(username="ocr_user", password="pw", role="user")

        cls.ready = cls._doc_with_version(
            "OCR OK",
            processing_state=DocumentVersion.ProcessingState.READY,
            ocr_status=OCRStatus.SUCCESS,
            ocr_text="lesbarer Text",
        )
        cls.empty = cls._doc_with_version(
            "OCR leer",
            processing_state=DocumentVersion.ProcessingState.READY,
            ocr_status=OCRStatus.SUCCESS,
            ocr_text="",
        )
        cls.failed = cls._doc_with_version(
            "Processing kaputt",
            processing_state=DocumentVersion.ProcessingState.FAILED,
            processing_failed_step="ocr",
            processing_error="boom",
            ocr_status=OCRStatus.FAILED,
            ocr_error="ocr boom",
            ocr_text="",
        )

    @staticmethod
    def _doc_with_version(title, **version_kwargs):
        doc = Document.objects.create(title=title)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=f"/data/originals/{title}.pdf",
            sha256=title.encode().hex().ljust(64, "0")[:64],
            **version_kwargs,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_non_admin_forbidden(self):
        self.client.force_authenticate(self.user)

        resp = self.client.get("/api/system/ocr-health/")

        self.assertEqual(resp.status_code, 403)

    def test_ocr_health_summary_and_issues(self):
        self.client.force_authenticate(self.admin)

        resp = self.client.get("/api/system/ocr-health/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "error")
        self.assertEqual(resp.data["summary"]["total_current_versions"], 3)
        self.assertEqual(resp.data["summary"]["ocr_failed"], 1)
        self.assertEqual(resp.data["summary"]["empty_ocr_text"], 2)
        self.assertEqual(resp.data["summary"]["processing_failed"], 1)
        issue_titles = {row["document_title"] for row in resp.data["issues"]}
        self.assertIn("OCR leer", issue_titles)
        self.assertIn("Processing kaputt", issue_titles)

    def test_bulk_retry_failed_queues_only_failed_current_versions(self):
        from unittest import mock

        self.client.force_authenticate(self.admin)
        with mock.patch("documents.views.retry_document_version.delay") as delayed:
            resp = self.client.post("/api/system/ocr-health/retry-failed/", {"limit": 10})

        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.data["queued"], 1)
        version = self.failed.current_version
        delayed.assert_called_once_with(version.id, actor_id=self.admin.id)
