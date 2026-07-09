from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import Dossier, Document, DocumentPageText, DocumentVersion

User = get_user_model()


def make_doc(owner, title, text):
    doc = Document.objects.create(title=title, owner=owner)
    version = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path="/tmp/dossier.pdf",
        sha256="c" * 64,
        ocr_text=text,
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    DocumentPageText.objects.create(version=version, page_no=1, text=text)
    return doc


class DossierApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="dossier-user", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="dossier-other", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="dossier-guest", password="pw", role="guest"
        )
        cls.own_doc = make_doc(
            cls.user,
            "Helvetia Polizze",
            "Helvetia Versicherung Polizze fuer Cornelia mit Praemie 12,50 Euro.",
        )
        cls.foreign_doc = make_doc(
            cls.other,
            "Fremde Helvetia Polizze",
            "Helvetia Versicherung fremder Inhalt darf nicht ins Dossier.",
        )

    def test_generate_dossier_uses_only_visible_documents(self):
        self.client.force_authenticate(self.user)
        create_resp = self.client.post(
            "/api/dossiers/",
            {"title": "Helvetia", "query": "Alles zur Helvetia Polizze"},
            format="json",
        )
        self.assertEqual(create_resp.status_code, 201)

        resp = self.client.post(f"/api/dossiers/{create_resp.data['id']}/generate/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], Dossier.Status.GENERATED)
        source_doc_ids = {source["document"] for source in resp.data["sources"]}
        self.assertIn(self.own_doc.id, source_doc_ids)
        self.assertNotIn(self.foreign_doc.id, source_doc_ids)
        self.assertTrue(resp.data["summary"])

    def test_export_markdown_contains_sources(self):
        self.client.force_authenticate(self.user)
        dossier = Dossier.objects.create(
            owner=self.user,
            title="Export",
            query="Helvetia",
            status=Dossier.Status.GENERATED,
            summary="Kurzfassung [S1].",
            sources=[
                {
                    "id": "S1",
                    "document": self.own_doc.id,
                    "document_title": self.own_doc.title,
                    "page": 1,
                    "snippet": "Helvetia Versicherung",
                }
            ],
        )
        dossier.documents.add(self.own_doc)

        resp = self.client.get(f"/api/dossiers/{dossier.id}/export-markdown/")

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn("# Export", body)
        self.assertIn("Helvetia Versicherung", body)

    def test_guest_cannot_generate(self):
        dossier = Dossier.objects.create(
            owner=self.guest,
            title="Gast",
            query="Helvetia",
        )
        self.client.force_authenticate(self.guest)

        resp = self.client.post(f"/api/dossiers/{dossier.id}/generate/")

        self.assertEqual(resp.status_code, 403)
