import os
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    Correspondent,
    Document,
    DocumentFolder,
    DocumentReviewTask,
    DocumentType,
    DocumentVersion,
    OCRStatus,
    StoragePath,
    Tag,
)

User = get_user_model()


class DocumentQualityApiTests(APITestCase):
    """Das Qualitätscenter bleibt deterministisch und owner-gescopet."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="dms-quality-test-")
        self.owner = User.objects.create_user(
            username="quality_owner",
            password="pw",
            role="user",
        )
        self.other = User.objects.create_user(
            username="quality_other",
            password="pw",
            role="user",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_quality_status_marks_weak_document_with_actionable_issues(self):
        document = self._weak_document(owner=self.owner)

        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/documents/quality-status/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "error")
        self.assertEqual(response.data["summary"]["documents"], 1)
        self.assertEqual(response.data["summary"]["critical"], 1)
        self.assertGreaterEqual(response.data["summary"]["ocr_issues"], 1)
        self.assertGreaterEqual(response.data["summary"]["metadata_issues"], 1)
        self.assertGreaterEqual(response.data["summary"]["archive_issues"], 1)

        issue = response.data["issues"][0]
        self.assertEqual(issue["document_id"], document.id)
        self.assertLess(issue["score"], 60)
        categories = {item["category"] for item in issue["issues"]}
        self.assertIn("ocr", categories)
        self.assertIn("metadata", categories)
        self.assertIn("archive", categories)
        self.assertIn("review", categories)

    def test_quality_status_is_owner_scoped(self):
        own = self._strong_document(owner=self.owner, title="Gute Polizze")
        self._weak_document(owner=self.other, title="scan-deadbeef")

        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/documents/quality-status/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["documents"], 1)
        self.assertEqual(response.data["issues"], [])

        detail = self.client.get(f"/api/documents/{own.id}/quality/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["grade"], "excellent")
        self.assertEqual(detail.data["status"], "ok")
        self.assertGreaterEqual(detail.data["score"], 95)

    def test_quality_detail_hides_foreign_documents(self):
        foreign = self._strong_document(owner=self.other, title="Fremdes Dokument")

        self.client.force_authenticate(self.owner)
        response = self.client.get(f"/api/documents/{foreign.id}/quality/")

        self.assertEqual(response.status_code, 404)

    def _weak_document(self, *, owner, title="scan-a1b2c3d4"):
        document = Document.objects.create(title=title, owner=owner)
        version = DocumentVersion.objects.create(
            document=document,
            version_no=1,
            file_path=f"/data/originals/{title}.pdf",
            sha256=("a" * 64),
            processing_state=DocumentVersion.ProcessingState.READY,
            ocr_status=OCRStatus.SUCCESS,
            ocr_text="",
            page_count=3,
        )
        document.current_version = version
        document.save(update_fields=["current_version"])
        DocumentReviewTask.objects.create(
            document=document,
            kind=DocumentReviewTask.Kind.OCR_EMPTY,
            signature=f"ocr-empty:{document.id}",
            priority=20,
            message="OCR leer.",
            suggested_action="OCR erneut ausführen",
        )
        return document

    def _strong_document(self, *, owner, title):
        archive_path = self._touch(f"{title}-archive.pdf")
        thumbnail_path = self._touch(f"{title}-thumb.jpg")
        correspondent = Correspondent.objects.create(name=f"{title} GmbH")
        document_type = DocumentType.objects.create(name=f"{title} Typ")
        storage_path = StoragePath.objects.create(name=f"{title} Ablage")
        folder = DocumentFolder.objects.create(name=f"{title} Ordner")
        tag = Tag.objects.create(name=f"{title} Tag")
        document = Document.objects.create(
            title=title,
            owner=owner,
            created_at=timezone.now(),
            correspondent=correspondent,
            document_type=document_type,
            storage_path=storage_path,
            folder=folder,
            archive_status=Document.ArchiveStatus.OK,
            archive_checked_at=timezone.now(),
            review_status=Document.ReviewStatus.REVIEWED,
        )
        document.tags.add(tag)
        version = DocumentVersion.objects.create(
            document=document,
            version_no=1,
            file_path=f"/data/originals/{title}.pdf",
            archive_path=archive_path,
            thumbnail_path=thumbnail_path,
            sha256=("b" * 64),
            processing_state=DocumentVersion.ProcessingState.READY,
            ocr_status=OCRStatus.SUCCESS,
            ocr_text="Dies ist ein sauber erkanntes Dokument mit ausreichend Text.",
            page_count=1,
            is_immutable=True,
            metadata_snapshot={"title": title, "tags": [tag.name]},
            snapshot_schema_version=1,
            snapshot_taken_at=timezone.now(),
            seal_hash=("c" * 64),
        )
        document.current_version = version
        document.save(update_fields=["current_version"])
        return document

    def _touch(self, filename):
        path = os.path.join(self.tmpdir, filename.replace(" ", "-"))
        with open(path, "wb") as handle:
            handle.write(b"ok")
        return path
