"""P1: Der zentrale Ingest (create_document_from_file) ist atomar.

Scheitert ein spaeterer DB-Schritt, duerfen keine unvollstaendigen Dokumente/
Versionen (oder Versionen ohne Audit) zurueckbleiben, und die bereits
geschriebene Originaldatei wird entfernt statt zu verwaisen.
"""
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import pipeline
from .models import AuditLogEntry, Document, DocumentVersion

User = get_user_model()


class IngestAtomicityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ingest_u", password="pw", role="user")
        self.tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        self.tmp.write(b"%PDF-1.7\n" + b"0" * 40)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        self.addCleanup(lambda: self.path.exists() and self.path.unlink())

    def test_fehler_im_audit_schritt_rollt_alles_zurueck_und_loescht_datei(self):
        with mock.patch.object(
            AuditLogEntry.objects, "create", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                pipeline.create_document_from_file(
                    str(self.path), title="Kaputt", owner=self.user
                )

        # Kein Teil-Zustand: weder Dokument noch Version bleiben zurueck.
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(DocumentVersion.objects.count(), 0)
        # Verwaiste Originaldatei wurde entfernt.
        self.assertFalse(
            self.path.exists(), "Originaldatei haette entfernt werden muessen."
        )

    def test_erfolgsfall_legt_dokument_version_und_audit_an(self):
        document, version = pipeline.create_document_from_file(
            str(self.path), title="Gut", owner=self.user
        )
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(document.current_version_id, version.id)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="upload", object_id=str(document.id)
            ).exists()
        )
        # Erfolgsfall laesst die Datei bestehen.
        self.assertTrue(self.path.exists())
