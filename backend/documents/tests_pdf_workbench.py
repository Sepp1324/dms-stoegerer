import tempfile
from pathlib import Path
from unittest import mock

import pikepdf
from django.contrib.auth import get_user_model
from pikepdf import Name
from rest_framework.test import APITestCase

from . import pipeline, storage
from .models import AuditLogEntry, Document, DocumentVersion, Tag

User = get_user_model()
ROTATE_NAME = Name("/Rotate")


class PdfWorkbenchTests(APITestCase):
    """PDF-Werkbank: Seitenoperationen erzeugen neue Versionen/Dokumente."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.originals = Path(self.tmp.name) / "originals"
        self.originals.mkdir(parents=True, exist_ok=True)
        self.storage_patch = mock.patch.object(storage, "ORIGINALS_DIR", self.originals)
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)

        self.user = User.objects.create_user(
            username="pdf-workbench-user", password="pw", role="user"
        )
        self.other = User.objects.create_user(
            username="pdf-workbench-other", password="pw", role="user"
        )
        self.guest = User.objects.create_user(
            username="pdf-workbench-guest", password="pw", role="guest"
        )

    def _pdf(self, name: str, pages: int) -> Path:
        path = self.originals / name
        pdf = pikepdf.Pdf.new()
        for _idx in range(pages):
            pdf.add_blank_page(page_size=(72, 72))
        pdf.save(path)
        pdf.close()
        return path

    def _doc(self, title: str, owner, pages: int = 3) -> Document:
        path = self._pdf(f"{title}.pdf", pages)
        doc = Document.objects.create(title=title, owner=owner)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=str(path),
            sha256=pipeline.sha256_of(path),
            mime_type="application/pdf",
            size=path.stat().st_size,
            page_count=pages,
            processing_state=DocumentVersion.ProcessingState.READY,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_pages_manifest_reads_current_pdf(self):
        doc = self._doc("manifest", self.user, pages=3)
        self.client.force_authenticate(self.user)

        resp = self.client.get(f"/api/documents/{doc.id}/pdf-workbench/pages/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["page_count"], 3)
        self.assertEqual([page["page"] for page in resp.data["pages"]], [1, 2, 3])

    def test_rewrite_creates_new_version_with_reordered_rotated_pages(self):
        doc = self._doc("rewrite", self.user, pages=3)
        self.client.force_authenticate(self.user)

        with mock.patch("documents.views.process_document_version.delay") as delay:
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/rewrite/",
                {
                    "pages": [
                        {"page": 3},
                        {"page": 1, "rotation": 90},
                    ],
                    "reason": "Test",
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 201)
        doc.refresh_from_db()
        self.assertEqual(doc.versions.count(), 2)
        self.assertEqual(doc.current_version.version_no, 2)
        delay.assert_called_once_with(doc.current_version.id)
        with pikepdf.open(doc.current_version.file_path) as pdf:
            self.assertEqual(len(pdf.pages), 2)
            self.assertEqual(int(pdf.pages[1].obj.get(ROTATE_NAME, 0) or 0), 90)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="pdf_workbench_rewrite",
                object_id=str(doc.id),
            ).exists()
        )

    def test_rewrite_invalid_page_returns_400_without_version(self):
        doc = self._doc("invalid", self.user, pages=2)
        self.client.force_authenticate(self.user)

        resp = self.client.post(
            f"/api/documents/{doc.id}/pdf-workbench/rewrite/",
            {"pages": [{"page": 9}]},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(doc.versions.count(), 1)

    def test_split_creates_new_documents_and_copies_tags(self):
        doc = self._doc("split-source", self.user, pages=4)
        tag = Tag.objects.create(name="Werkbank", color="#93c5fd")
        doc.tags.add(tag)
        self.client.force_authenticate(self.user)

        with mock.patch("documents.views.process_document_version.delay") as delay:
            resp = self.client.post(
                f"/api/documents/{doc.id}/pdf-workbench/split/",
                {
                    "parts": [
                        {"title": "Teil A", "pages": [1, 2]},
                        {"title": "Teil B", "pages": [3, 4]},
                    ]
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.data["documents"]), 2)
        created = Document.objects.filter(title__in=["Teil A", "Teil B"]).order_by("title")
        self.assertEqual(created.count(), 2)
        self.assertTrue(all(item.owner == self.user for item in created))
        self.assertTrue(all(tag in item.tags.all() for item in created))
        self.assertEqual(delay.call_count, 2)
        with pikepdf.open(created[0].current_version.file_path) as pdf:
            self.assertEqual(len(pdf.pages), 2)
        self.assertTrue(
            AuditLogEntry.objects.filter(action="pdf_workbench_split").exists()
        )

    def test_merge_creates_new_version_and_respects_owner_scope(self):
        target = self._doc("merge-target", self.user, pages=2)
        appendix = self._doc("merge-appendix", self.user, pages=1)
        foreign = self._doc("merge-foreign", self.other, pages=1)
        self.client.force_authenticate(self.user)

        blocked = self.client.post(
            f"/api/documents/{target.id}/pdf-workbench/merge/",
            {"document_ids": [foreign.id]},
            format="json",
        )
        self.assertEqual(blocked.status_code, 404)

        with mock.patch("documents.views.process_document_version.delay") as delay:
            resp = self.client.post(
                f"/api/documents/{target.id}/pdf-workbench/merge/",
                {"document_ids": [appendix.id]},
                format="json",
            )

        self.assertEqual(resp.status_code, 201)
        target.refresh_from_db()
        delay.assert_called_once_with(target.current_version.id)
        with pikepdf.open(target.current_version.file_path) as pdf:
            self.assertEqual(len(pdf.pages), 3)
        self.assertTrue(
            AuditLogEntry.objects.filter(action="pdf_workbench_merge").exists()
        )

    def test_guest_cannot_write_pdf_workbench_actions(self):
        doc = self._doc("guest", self.guest, pages=2)
        self.client.force_authenticate(self.guest)

        resp = self.client.post(
            f"/api/documents/{doc.id}/pdf-workbench/rewrite/",
            {"pages": [{"page": 1}]},
            format="json",
        )

        self.assertEqual(resp.status_code, 403)
