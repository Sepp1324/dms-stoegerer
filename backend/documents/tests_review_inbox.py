from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import (
    AuditLogEntry,
    CaseFileCandidate,
    ClassificationRule,
    Correspondent,
    Document,
    DocumentFolder,
    DocumentType,
    DocumentVersion,
    ExtractionCandidate,
    Tag,
)

User = get_user_model()


class DocumentReviewInboxTests(APITestCase):
    """Review-Inbox: fachliche Prüfung ist getrennt von der Pipeline-State-Machine."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="review_owner", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="review_other", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="review_guest", password="pw", role="guest"
        )

        cls.needs_review = cls._document(
            "Inbox Dokument",
            owner=cls.owner,
            review_status=Document.ReviewStatus.NEEDS_REVIEW,
        )
        cls.reviewed = cls._document(
            "Geprüftes Dokument",
            owner=cls.owner,
            review_status=Document.ReviewStatus.REVIEWED,
        )

    @classmethod
    def _document(cls, title, *, owner, review_status):
        doc = Document.objects.create(
            title=title,
            owner=owner,
            review_status=review_status,
        )
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=f"/data/originals/{title}.pdf",
            sha256=title.encode().hex().ljust(64, "0")[:64],
            processing_state=DocumentVersion.ProcessingState.READY,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_new_document_defaults_to_needs_review(self):
        doc = Document.objects.create(title="Neues Dokument", owner=self.owner)
        self.assertEqual(doc.review_status, Document.ReviewStatus.NEEDS_REVIEW)

    def test_serializer_liefert_review_status(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(f"/api/documents/{self.needs_review.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["review_status"], "needs_review")

    def test_liste_filtert_needs_review(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?review_status=needs_review")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            {doc["title"] for doc in resp.data["results"]},
            {"Inbox Dokument"},
        )

    def test_liste_filtert_reviewed(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?review_status=reviewed")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            {doc["title"] for doc in resp.data["results"]},
            {"Geprüftes Dokument"},
        )

    def test_unbekannter_review_filter_wird_ignoriert(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/?review_status=kaputt")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 2)

    def test_mark_reviewed_setzt_status_und_audit(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/documents/{self.needs_review.id}/mark_reviewed/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["review_status"], "reviewed")

        self.needs_review.refresh_from_db()
        self.assertEqual(self.needs_review.review_status, Document.ReviewStatus.REVIEWED)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="mark_reviewed",
                object_type="Document",
                object_id=str(self.needs_review.id),
                actor=self.owner,
            ).exists()
        )

    def test_mark_reviewed_ist_idempotent(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/documents/{self.reviewed.id}/mark_reviewed/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["review_status"], "reviewed")
        self.assertFalse(
            AuditLogEntry.objects.filter(
                action="mark_reviewed",
                object_id=str(self.reviewed.id),
            ).exists()
        )

    def test_mark_reviewed_gast_liefert_403(self):
        guest_doc = self._document(
            "Gast Inbox",
            owner=self.guest,
            review_status=Document.ReviewStatus.NEEDS_REVIEW,
        )
        self.client.force_authenticate(self.guest)
        resp = self.client.post(f"/api/documents/{guest_doc.id}/mark_reviewed/")
        self.assertEqual(resp.status_code, 403)

    def test_mark_reviewed_fremdes_dokument_liefert_404(self):
        self.client.force_authenticate(self.other)
        resp = self.client.post(f"/api/documents/{self.needs_review.id}/mark_reviewed/")
        self.assertEqual(resp.status_code, 404)

    def test_inbox_summary_ist_owner_scoped_und_zaehlt_kandidaten(self):
        ExtractionCandidate.objects.create(
            document=self.needs_review,
            field=ExtractionCandidate.Field.AMOUNT,
            value="12,50 EUR",
            normalized_value="12.50",
            status=ExtractionCandidate.Status.PENDING,
        )
        CaseFileCandidate.objects.create(
            document=self.needs_review,
            kind=CaseFileCandidate.Kind.NEW_CASE,
            suggested_title="Neue Akte",
            signature="new:review",
            score=58,
            status=CaseFileCandidate.Status.PENDING,
        )
        other_doc = self._document(
            "Fremde Inbox",
            owner=self.other,
            review_status=Document.ReviewStatus.NEEDS_REVIEW,
        )
        ExtractionCandidate.objects.create(
            document=other_doc,
            field=ExtractionCandidate.Field.AMOUNT,
            value="99,00 EUR",
            normalized_value="99.00",
            status=ExtractionCandidate.Status.PENDING,
        )

        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/documents/inbox-summary/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["total_needs_review"], 1)
        self.assertEqual(resp.data["ready"], 1)
        self.assertEqual(resp.data["pending_extraction_candidates"], 1)
        self.assertEqual(resp.data["pending_case_candidates"], 1)

    def test_mark_reviewed_mit_lernregel_legt_classification_rule_an(self):
        corr = Correspondent.objects.create(name="Wüstenrot")
        dtype = DocumentType.objects.create(name="Vertrag")
        folder = DocumentFolder.objects.create(name="Versicherungen")
        tag = Tag.objects.create(name="Wichtig")
        self.needs_review.correspondent = corr
        self.needs_review.document_type = dtype
        self.needs_review.folder = folder
        self.needs_review.save(update_fields=["correspondent", "document_type", "folder"])
        self.needs_review.tags.add(tag)

        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/documents/{self.needs_review.id}/mark_reviewed/",
            {"create_rule": True, "match_text": "Wüstenrot Gruppe"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["review_status"], "reviewed")
        self.assertTrue(resp.data["learned_rule_created"])
        rule = ClassificationRule.objects.get(id=resp.data["learned_rule"]["id"])
        self.assertEqual(rule.match, {"text_contains": ["Wüstenrot Gruppe"]})
        self.assertEqual(rule.then["correspondent"], "Wüstenrot")
        self.assertEqual(rule.then["document_type"], "Vertrag")
        self.assertEqual(rule.then["folder"], "Versicherungen")
        self.assertEqual(rule.then["tags"], ["Wichtig"])
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="create_classification_rule_from_review",
                object_id=str(self.needs_review.id),
                actor=self.owner,
            ).exists()
        )

    def test_mark_reviewed_lernregel_braucht_match_text(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/documents/{self.needs_review.id}/mark_reviewed/",
            {"create_rule": True, "match_text": ""},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.needs_review.refresh_from_db()
        self.assertEqual(
            self.needs_review.review_status,
            Document.ReviewStatus.NEEDS_REVIEW,
        )

    def test_mark_reviewed_bulk_schliesst_mehrere_eigene_dokumente(self):
        other_doc = self._document(
            "Fremdes Bulk-Dokument",
            owner=self.other,
            review_status=Document.ReviewStatus.NEEDS_REVIEW,
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            "/api/documents/mark-reviewed-bulk/",
            {"ids": [self.needs_review.id, self.reviewed.id, other_doc.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["updated"], 1)
        self.assertEqual(resp.data["unchanged"], 1)
        self.assertEqual(
            resp.data["errors"],
            [{"id": other_doc.id, "error": "nicht gefunden oder keine Berechtigung"}],
        )
        self.needs_review.refresh_from_db()
        self.assertEqual(self.needs_review.review_status, Document.ReviewStatus.REVIEWED)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="mark_reviewed_bulk",
                object_id="1 Dokumente",
                actor=self.owner,
            ).exists()
        )

    def test_inbox_generate_candidates_verarbeitet_nur_sichtbare_dokumente(self):
        other_doc = self._document(
            "Fremde Kandidaten",
            owner=self.other,
            review_status=Document.ReviewStatus.NEEDS_REVIEW,
        )
        self.client.force_authenticate(self.owner)

        with (
            patch("documents.services.extraction.generate_candidates", return_value=2)
            as extract_mock,
            patch("documents.services.case_matching.generate_candidates", return_value=1)
            as case_mock,
        ):
            resp = self.client.post(
                "/api/documents/inbox-generate-candidates/",
                {"ids": [self.needs_review.id, other_doc.id]},
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["documents"], 1)
        self.assertEqual(resp.data["extraction_created"], 2)
        self.assertEqual(resp.data["case_created"], 1)
        self.assertEqual(
            resp.data["errors"],
            [{"id": other_doc.id, "error": "nicht gefunden oder keine Berechtigung"}],
        )
        extract_mock.assert_called_once()
        case_mock.assert_called_once()
