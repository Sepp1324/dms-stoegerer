"""P2: Ein Broker-Ausfall NACH READY darf den Verarbeitungs-Task nicht killen.

suggest_document_metadata.delay() wird nach erfolgreicher Pipeline angestoßen. Ist
Redis in diesem Moment weg, ist das Dokument bereits READY – der Task darf NICHT
als fehlgeschlagen enden (sonst erneute Verarbeitung, Vorschlag trotzdem weg).
"""
from unittest import mock

from django.test import TestCase

from . import tasks
from .models import Document, DocumentVersion


class PostReadyEnqueueGuardTests(TestCase):
    def _version(self):
        doc = Document.objects.create(title="X")
        return DocumentVersion.objects.create(
            document=doc, version_no=1, file_path="/tmp/x.pdf", sha256="a" * 64
        )

    @mock.patch("documents.tasks._maybe_dispatch_flashcards")
    @mock.patch("documents.pipeline.process_version", return_value={"status": "done"})
    @mock.patch(
        "ai.tasks.suggest_document_metadata.delay", side_effect=RuntimeError("broker down")
    )
    def test_process_version_ueberlebt_broker_ausfall(self, delay, proc, flash):
        version = self._version()
        # Darf NICHT werfen, obwohl der Enqueue scheitert.
        result = tasks.process_document_version(version.id)
        self.assertEqual(result["status"], "done")
        delay.assert_called_once()

    @mock.patch("documents.pipeline.retry_version", return_value={"status": "done"})
    @mock.patch(
        "ai.tasks.suggest_document_metadata.delay", side_effect=RuntimeError("broker down")
    )
    def test_retry_version_ueberlebt_broker_ausfall(self, delay, retry):
        version = self._version()
        result = tasks.retry_document_version(version.id)
        self.assertEqual(result["status"], "done")
        delay.assert_called_once()
