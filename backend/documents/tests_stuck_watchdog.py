"""Tests für den Stuck-Task-Watchdog (reap_stuck_versions).

Da acks_late bewusst aus ist, kann ein Worker-Crash eine Version in einem
Zwischenzustand (z. B. OCR_RUNNING) hinterlassen. Der Watchdog macht sie wieder
verarbeitbar: Zwischenzustände -> FAILED (retry-fähig), hängendes SEALED -> READY.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from . import tasks
from .models import Document, DocumentVersion

User = get_user_model()
PS = DocumentVersion.ProcessingState


@override_settings(PROCESSING_STUCK_AFTER_MINUTES=30)
class ReapStuckVersionsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reap", password="pw")

    def _version(self, state, changed_ago_min):
        doc = Document.objects.create(title="d", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path="/tmp/reap.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            processing_state=state,
            created_by=self.user,
        )
        # Zeitstempel künstlich altern lassen (per update, ohne save-Guard).
        DocumentVersion.objects.filter(pk=version.pk).update(
            processing_state_changed_at=timezone.now()
            - timedelta(minutes=changed_ago_min)
        )
        return version

    def test_haengende_version_wird_failed(self):
        v = self._version(PS.OCR_RUNNING, changed_ago_min=60)
        tasks.reap_stuck_versions()
        v.refresh_from_db()
        self.assertEqual(v.processing_state, PS.FAILED)
        self.assertEqual(v.processing_failed_step, "watchdog")

    def test_frische_version_bleibt_unberuehrt(self):
        v = self._version(PS.OCR_RUNNING, changed_ago_min=5)
        tasks.reap_stuck_versions()
        v.refresh_from_db()
        self.assertEqual(v.processing_state, PS.OCR_RUNNING)

    def test_haengendes_sealed_wird_ready(self):
        v = self._version(PS.SEALED, changed_ago_min=60)
        tasks.reap_stuck_versions()
        v.refresh_from_db()
        self.assertEqual(v.processing_state, PS.READY)

    def test_terminale_zustaende_unberuehrt(self):
        ready = self._version(PS.READY, changed_ago_min=60)
        failed = self._version(PS.FAILED, changed_ago_min=60)
        result = tasks.reap_stuck_versions()
        ready.refresh_from_db()
        failed.refresh_from_db()
        self.assertEqual(ready.processing_state, PS.READY)
        self.assertEqual(failed.processing_state, PS.FAILED)
        self.assertEqual(result["reaped"], 0)
        self.assertEqual(result["completed"], 0)

    def test_uploaded_haengt_auch_reap(self):
        # Task nie gelaufen (Broker war weg, aber enqueue_processing markierte
        # nicht FAILED, z. B. anderer Pfad): UPLOADED zu lange -> FAILED.
        v = self._version(PS.UPLOADED, changed_ago_min=60)
        tasks.reap_stuck_versions()
        v.refresh_from_db()
        self.assertEqual(v.processing_state, PS.FAILED)
