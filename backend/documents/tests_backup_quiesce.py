"""P1: Backup-Schreibsperre (Quiesce). Während einer Sicherung darf kein neues
Dokument geschrieben werden, damit /data-Snapshot und DB-Dump denselben Zustand
sehen. Getestet: Helfer-Flag, Storage-Backstop, HTTP-503, Beat-Task-Skip, Command."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APITestCase

from documents import storage, tasks
from documents.services import quiesce

User = get_user_model()


class _FakeRedis:
    """Minimaler Redis-Ersatz für den Quiesce-Helfer (exists/set/delete)."""

    def __init__(self):
        self.store = {}

    def exists(self, key):
        return 1 if key in self.store else 0

    def set(self, key, value, ex=None):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


class QuiesceHelperTests(TestCase):
    def test_set_und_is_quiesced_roundtrip(self):
        fake = _FakeRedis()
        with mock.patch.object(quiesce, "_client", return_value=fake):
            self.assertFalse(quiesce.is_quiesced())
            quiesce.set_quiesce(True, ttl=60)
            self.assertTrue(quiesce.is_quiesced())
            quiesce.set_quiesce(False)
            self.assertFalse(quiesce.is_quiesced())

    def test_is_quiesced_fail_open_bei_redis_fehler(self):
        with mock.patch.object(
            quiesce, "_client", side_effect=ConnectionError("redis down")
        ):
            # Redis weg -> NICHT sperren (Schreibpfade dürfen nicht daran scheitern).
            self.assertFalse(quiesce.is_quiesced())

    def test_raise_if_quiesced(self):
        with mock.patch.object(quiesce, "is_quiesced", return_value=True):
            with self.assertRaises(quiesce.BackupQuiesceActive):
                quiesce.raise_if_quiesced()


class QuiesceStorageBackstopTests(TestCase):
    def test_save_upload_gesperrt(self):
        with mock.patch.object(quiesce, "is_quiesced", return_value=True):
            with self.assertRaises(quiesce.BackupQuiesceActive):
                storage.save_upload(
                    SimpleUploadedFile("x.pdf", b"%PDF-1.4", "application/pdf")
                )

    def test_save_bytes_gesperrt(self):
        with mock.patch.object(quiesce, "is_quiesced", return_value=True):
            with self.assertRaises(quiesce.BackupQuiesceActive):
                storage.save_bytes(b"%PDF-1.4", ext="pdf")


class QuiesceHttpUploadTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("q", password="pw", role="user")
        self.client.force_authenticate(self.user)

    def test_upload_liefert_503_bei_schreibsperre(self):
        with mock.patch.object(quiesce, "is_quiesced", return_value=True):
            resp = self.client.post(
                "/api/documents/upload/",
                {"file": SimpleUploadedFile("x.pdf", b"%PDF-1.4", "application/pdf")},
                format="multipart",
            )
        self.assertEqual(resp.status_code, 503)


class QuiesceBeatTaskSkipTests(TestCase):
    def test_scan_consume_folder_uebersprungen(self):
        with mock.patch.object(tasks, "is_quiesced", return_value=True):
            result = tasks.scan_consume_folder()
        self.assertEqual(result.get("skipped"), "backup_quiesce")

    def test_fetch_all_mail_accounts_uebersprungen(self):
        with mock.patch.object(tasks, "is_quiesced", return_value=True):
            result = tasks.fetch_all_mail_accounts()
        self.assertEqual(result.get("skipped"), "backup_quiesce")
        self.assertEqual(result.get("dispatched"), 0)


class QuiesceCommandTests(TestCase):
    # WICHTIG: Das Command importiert set_quiesce in SEINEN Namespace
    # (from ... import set_quiesce). Daher dort patchen, nicht im quiesce-Modul.
    CMD = "documents.management.commands.backup_quiesce.set_quiesce"

    def test_command_on_setzt_flag(self):
        with mock.patch(self.CMD) as set_mock:
            call_command("backup_quiesce", "--on", "--ttl", "120")
        set_mock.assert_called_once_with(True, ttl=120)

    def test_command_off_loest_flag(self):
        with mock.patch(self.CMD) as set_mock:
            call_command("backup_quiesce", "--off")
        set_mock.assert_called_once_with(False)
