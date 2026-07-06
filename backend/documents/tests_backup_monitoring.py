from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from documents.models import BackupMonitor, BackupRun


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
