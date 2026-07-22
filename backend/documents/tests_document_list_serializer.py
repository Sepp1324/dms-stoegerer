"""P2: Die Dokumentliste liefert NICHT mehr die vollständige Versionshistorie
(schlanker DocumentListSerializer); die Detailansicht dagegen weiterhin schon."""
import hashlib

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from documents.models import Document, DocumentVersion

User = get_user_model()


class DocumentListSerializerTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("lister", password="pw", role="user")
        cls.doc = Document.objects.create(title="Mehrfach versioniert", owner=cls.user)
        for n in (1, 2, 3):
            v = DocumentVersion.objects.create(
                document=cls.doc, version_no=n, file_path=f"/tmp/v{n}.pdf",
                sha256=hashlib.sha256(str(n).encode()).hexdigest(), ocr_text="x",
            )
        cls.doc.current_version = v
        cls.doc.save(update_fields=["current_version"])

    def setUp(self):
        self.client.force_authenticate(self.user)

    def test_liste_ohne_versions_aber_mit_rollups(self):
        resp = self.client.get("/api/documents/")
        self.assertEqual(resp.status_code, 200)
        row = next(d for d in resp.data["results"] if d["id"] == self.doc.id)
        self.assertNotIn("versions", row)  # keine Historie in der Liste
        # Die für Listen-Badges nötigen Rollup-Felder bleiben vorhanden:
        self.assertIn("processing_state", row)
        self.assertIn("ocr_status", row)

    def test_detail_enthaelt_volle_versionshistorie(self):
        resp = self.client.get(f"/api/documents/{self.doc.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("versions", resp.data)
        self.assertEqual(len(resp.data["versions"]), 3)
