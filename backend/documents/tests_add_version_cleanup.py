"""P2: Scheitert das Anlegen einer neuen Version, verwaist die Upload-Datei nicht.

``save_upload`` schreibt die Datei VOR der DB-Transaktion. Scheitert
``create_version_for_document``, muss die Datei entfernt werden (wie beim
Erst-Ingest ``create_document_from_file``).
"""
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from . import pipeline, storage
from .models import Document

User = get_user_model()


class AddVersionCleanupTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("av", password="pw", role="user")
        self.doc = Document.objects.create(title="Doc", owner=self.user)
        self.client.force_authenticate(self.user)

    def test_fehler_beim_versionsanlegen_entfernt_datei(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"%PDF-1.4")
        tmp.close()
        saved = Path(tmp.name)
        self.addCleanup(lambda: saved.exists() and saved.unlink())
        self.assertTrue(saved.exists())

        self.client.raise_request_exception = False
        with mock.patch.object(
            storage, "save_upload", return_value=(str(saved), 8, "application/pdf")
        ), mock.patch.object(
            pipeline, "create_version_for_document", side_effect=RuntimeError("boom")
        ):
            resp = self.client.post(
                f"/api/documents/{self.doc.id}/add_version/",
                {"file": SimpleUploadedFile("x.pdf", b"%PDF-1.4", "application/pdf")},
                format="multipart",
            )

        self.assertEqual(resp.status_code, 500)
        self.assertFalse(saved.exists())  # verwaiste Datei wurde entfernt
