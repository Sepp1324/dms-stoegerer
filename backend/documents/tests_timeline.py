from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import AuditLogEntry, Document, DocumentVersion

User = get_user_model()


class DocumentTimelineApiTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="timeline-owner", password="pw", role="user"
        )
        self.other = User.objects.create_user(
            username="timeline-other", password="pw", role="user"
        )
        self.document = Document.objects.create(
            title="Timeline Dokument",
            owner=self.owner,
        )
        self.version = DocumentVersion.objects.create(
            document=self.document,
            version_no=1,
            file_path="/tmp/timeline.pdf",
            sha256="a" * 64,
        )
        self.document.current_version = self.version
        self.document.save(update_fields=["current_version"])

    def test_timeline_aggregates_document_and_version_audit_entries(self):
        AuditLogEntry.objects.create(
            actor=self.owner,
            action="update",
            object_type="Document",
            object_id=str(self.document.id),
            detail={"changes": {"title": {"from": "Alt", "to": "Neu"}}},
        )
        AuditLogEntry.objects.create(
            actor=None,
            action="processing_failed",
            object_type="DocumentVersion",
            object_id=str(self.version.id),
            detail={"step": "ocr", "error": "OCR exit 2"},
        )

        self.client.force_authenticate(self.owner)
        response = self.client.get(f"/api/documents/{self.document.id}/timeline/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        actions = [item["action"] for item in response.data["results"]]
        self.assertIn("update", actions)
        self.assertIn("processing_failed", actions)

    def test_timeline_maps_category_severity_and_summary(self):
        AuditLogEntry.objects.create(
            actor=self.owner,
            action="update",
            object_type="Document",
            object_id=str(self.document.id),
            detail={"changes": {"document_type": {"from": None, "to": "Rechnung"}}},
        )
        AuditLogEntry.objects.create(
            actor=None,
            action="processing_failed",
            object_type="DocumentVersion",
            object_id=str(self.version.id),
            detail={"step": "ocr", "error": "OCR exit 2"},
        )

        self.client.force_authenticate(self.owner)
        response = self.client.get(f"/api/documents/{self.document.id}/timeline/")

        failed = next(
            item
            for item in response.data["results"]
            if item["action"] == "processing_failed"
        )
        update = next(
            item for item in response.data["results"] if item["action"] == "update"
        )
        self.assertEqual(failed["category"], "processing")
        self.assertEqual(failed["severity"], "error")
        self.assertIn("ocr", failed["summary"])
        self.assertEqual(update["category"], "metadata")
        self.assertEqual(update["severity"], "info")
        self.assertIn("Typ", update["summary"])

    def test_timeline_is_owner_scoped(self):
        self.client.force_authenticate(self.other)

        response = self.client.get(f"/api/documents/{self.document.id}/timeline/")

        self.assertEqual(response.status_code, 404)
