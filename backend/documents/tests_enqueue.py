"""Tests für den gemeinsamen Enqueue-Pfad ``tasks.enqueue_processing``.

Kern von P1: Mail/Consume/Views nutzen denselben Helper. Bei einem Broker-Ausfall
wird die bereits committete Version als FAILED (Schritt ``hashing``) markiert –
retry-fähig statt für immer in UPLOADED zu hängen.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from kombu.exceptions import OperationalError

from . import tasks
from .models import Document, DocumentVersion

User = get_user_model()


class EnqueueProcessingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="enq", password="pw")
        doc = Document.objects.create(title="d", owner=self.user)
        self.version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path="/tmp/enq.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            created_by=self.user,
        )

    def test_erfolg_gibt_true_und_stoesst_task_an(self):
        with mock.patch(
            "documents.tasks.process_document_version.delay"
        ) as delay:
            ok = tasks.enqueue_processing(self.version)
        self.assertTrue(ok)
        delay.assert_called_once_with(self.version.id)
        self.version.refresh_from_db()
        self.assertEqual(
            self.version.processing_state,
            DocumentVersion.ProcessingState.UPLOADED,
        )

    def test_broker_down_markiert_failed_und_gibt_false(self):
        with mock.patch(
            "documents.tasks.process_document_version.delay",
            side_effect=OperationalError("broker down"),
        ):
            ok = tasks.enqueue_processing(self.version)
        self.assertFalse(ok)
        self.version.refresh_from_db()
        self.assertEqual(
            self.version.processing_state,
            DocumentVersion.ProcessingState.FAILED,
        )
        self.assertEqual(self.version.processing_failed_step, "hashing")
