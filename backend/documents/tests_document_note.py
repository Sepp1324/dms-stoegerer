"""Tests für die freie Dokument-Notiz (speichern + durchsuchbar)."""
import hashlib

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from documents.models import Document, DocumentVersion

User = get_user_model()


def _doc(owner, title, text="irrelevanter Inhalt"):
    doc = Document.objects.create(title=title, owner=owner)
    version = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path=f"/tmp/{title}.pdf",
        sha256=hashlib.sha256(title.encode()).hexdigest(),
        ocr_text=text,
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    return doc


class DocumentNoteTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("note-u", password="pw", role="user")
        cls.doc = _doc(cls.user, "Vertrag")

    def test_owner_can_save_note(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            f"/api/documents/{self.doc.id}/",
            {"note": "Kündigung abgeschickt am 2026-01-15"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["note"], "Kündigung abgeschickt am 2026-01-15")
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.note, "Kündigung abgeschickt am 2026-01-15")

    def test_note_is_searchable(self):
        self.doc.note = "Steuerbelegmarker"
        self.doc.save(update_fields=["note"])
        self.client.force_authenticate(self.user)

        resp = self.client.get("/api/documents/?q=Steuerbelegmarker")

        self.assertEqual(resp.status_code, 200)
        ids = [d["id"] for d in resp.data["results"]]
        self.assertIn(self.doc.id, ids)
