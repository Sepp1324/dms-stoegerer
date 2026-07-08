from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import AuditLogEntry, CaseFile, Document, DocumentVersion

User = get_user_model()


class CaseFileTests(APITestCase):
    """Vorgangsakten: Owner-Scope, Dokumentzuordnung und Zusammenfassung."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="case_owner", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="case_other", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="case_guest", password="pw", role="guest"
        )
        cls.owner_doc = cls._doc(
            "Wüstenrot Polizze",
            cls.owner,
            "Polizze Wüstenrot. Beitrag 225,74 Euro monatlich.",
        )
        cls.other_doc = cls._doc("Fremdes Dokument", cls.other, "Vertraulich")

    @classmethod
    def _doc(cls, title, owner, text):
        doc = Document.objects.create(title=title, owner=owner)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=f"/tmp/{title}.pdf",
            sha256=title.encode().hex().ljust(64, "0")[:64],
            ocr_text=text,
            processing_state=DocumentVersion.ProcessingState.READY,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_case_file_create_sets_owner_and_audit(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            "/api/case-files/",
            {"title": "Versicherung Wüstenrot", "description": "Polizzenvorgang"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        case_file = CaseFile.objects.get(id=resp.data["id"])
        self.assertEqual(case_file.owner, self.owner)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="case_file_create",
                object_type="CaseFile",
                object_id=str(case_file.id),
            ).exists()
        )

    def test_owner_scope_verhindert_fremde_akten(self):
        CaseFile.objects.create(title="Fremdakte", owner=self.other)
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/case-files/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["results"], [])

    def test_add_documents_ordner_nur_sichtbare_dokumente_zu(self):
        case_file = CaseFile.objects.create(title="Versicherung", owner=self.owner)
        self.client.force_authenticate(self.owner)

        resp = self.client.post(
            f"/api/case-files/{case_file.id}/add-documents/",
            {"ids": [self.owner_doc.id, self.other_doc.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.owner_doc.refresh_from_db()
        self.other_doc.refresh_from_db()
        self.assertEqual(self.owner_doc.case_file, case_file)
        self.assertIsNone(self.other_doc.case_file)
        self.assertEqual(resp.data["document_count"], 1)

    def test_remove_documents_entfernt_zuordnung(self):
        case_file = CaseFile.objects.create(title="Versicherung", owner=self.owner)
        self.owner_doc.case_file = case_file
        self.owner_doc.save(update_fields=["case_file"])
        self.client.force_authenticate(self.owner)

        resp = self.client.post(
            f"/api/case-files/{case_file.id}/remove-documents/",
            {"ids": [self.owner_doc.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.owner_doc.refresh_from_db()
        self.assertIsNone(self.owner_doc.case_file)
        self.assertEqual(resp.data["document_count"], 0)

    def test_summarize_speichert_fallback_summary_mit_quellen(self):
        case_file = CaseFile.objects.create(title="Versicherung", owner=self.owner)
        self.owner_doc.case_file = case_file
        self.owner_doc.save(update_fields=["case_file"])
        self.client.force_authenticate(self.owner)

        resp = self.client.post(f"/api/case-files/{case_file.id}/summarize/")

        self.assertEqual(resp.status_code, 200)
        case_file.refresh_from_db()
        self.assertTrue(case_file.ai_summary)
        self.assertIn(resp.data["source"], {"local", "unavailable", "ai", "error"})
        self.assertEqual(resp.data["sources"][0]["document"], self.owner_doc.id)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="case_file_summarize",
                object_id=str(case_file.id),
            ).exists()
        )

    def test_guest_darf_akten_nicht_schreiben(self):
        self.client.force_authenticate(self.guest)
        resp = self.client.post("/api/case-files/", {"title": "Nein"}, format="json")
        self.assertEqual(resp.status_code, 403)
