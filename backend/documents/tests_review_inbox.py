from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import AuditLogEntry, Document, DocumentVersion

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
