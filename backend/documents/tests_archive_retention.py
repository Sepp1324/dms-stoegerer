import hashlib
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from .models import AuditLogEntry, Document, DocumentVersion
from .services import archive, version_snapshot

User = get_user_model()


class ArchiveDocMixin:
    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()
        super().tearDown()

    def make_ready_document(
        self,
        owner=None,
        *,
        content=b"archiv-test",
        with_artifacts=False,
    ):
        doc = Document.objects.create(title="Archivdokument", owner=owner)
        path = Path(self.tmpdir.name) / f"doc-{doc.id}.pdf"
        path.write_bytes(content)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=str(path),
            archive_path=str(path) if with_artifacts else "",
            thumbnail_path=str(path) if with_artifacts else "",
            sha256=hashlib.sha256(content).hexdigest(),
            processing_state=DocumentVersion.ProcessingState.READY,
            is_immutable=False,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        version_snapshot.write_snapshot_on_seal(version, actor=owner)
        version.is_immutable = True
        version.save(update_fields=["is_immutable"])
        return doc, version, path


class ArchiveServiceTests(ArchiveDocMixin, TestCase):
    def test_verify_document_archive_ok_persists_status(self):
        doc, _version, _path = self.make_ready_document()

        report = archive.verify_document_archive(doc)

        self.assertEqual(report["status"], Document.ArchiveStatus.OK)
        doc.refresh_from_db()
        self.assertEqual(doc.archive_status, Document.ArchiveStatus.OK)
        self.assertTrue(doc.archive_checked_at)
        self.assertFalse(doc.archive_error)

    def test_verify_document_archive_detects_file_hash_mismatch(self):
        doc, _version, path = self.make_ready_document()
        path.write_bytes(b"nachtraeglich veraendert")

        report = archive.verify_document_archive(doc)

        self.assertEqual(report["status"], Document.ArchiveStatus.ERROR)
        self.assertFalse(report["integrity"]["chain_ok"])
        doc.refresh_from_db()
        self.assertEqual(doc.archive_status, Document.ArchiveStatus.ERROR)
        self.assertIn("Datei-Hash", doc.archive_error)


class ArchiveApiTests(ArchiveDocMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="archive-user", password="pw", role="user"
        )
        self.guest = User.objects.create_user(
            username="archive-guest", password="pw", role="guest"
        )
        self.admin = User.objects.create_user(
            username="archive-admin", password="pw", role="admin"
        )
        self.doc, self.version, self.path = self.make_ready_document(
            owner=self.user,
            with_artifacts=True,
        )

    def test_document_archive_check_action_persists_and_audits(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(f"/api/documents/{self.doc.id}/archive-check/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], Document.ArchiveStatus.OK)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.archive_status, Document.ArchiveStatus.OK)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="archive_check",
                object_type="Document",
                object_id=str(self.doc.id),
            ).exists()
        )

    def test_legal_hold_blocks_delete_before_retention_or_worm_checks(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            f"/api/documents/{self.doc.id}/legal-hold/",
            {"enabled": True, "reason": "Streitfall mit Versicherung"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["legal_hold"])

        delete_response = self.client.delete(f"/api/documents/{self.doc.id}/")

        self.assertEqual(delete_response.status_code, 403)
        self.assertIn("Legal Hold", str(delete_response.data["detail"]))
        self.assertTrue(Document.objects.filter(pk=self.doc.pk).exists())

    def test_guest_cannot_set_legal_hold(self):
        self.client.force_authenticate(self.guest)

        response = self.client.post(
            f"/api/documents/{self.doc.id}/legal-hold/",
            {"enabled": True, "reason": "nicht erlaubt"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_archive_health_is_admin_only(self):
        archive.verify_document_archive(self.doc)
        self.client.force_authenticate(self.user)
        denied = self.client.get("/api/system/archive-health/")
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/system/archive-health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["documents"], 1)
        self.assertEqual(response.data["summary"]["archive_ok"], 1)

    def test_evidence_status_respects_owner_scope(self):
        other_doc, _other_version, _other_path = self.make_ready_document(
            owner=self.admin,
            with_artifacts=True,
        )
        archive.verify_document_archive(self.doc)
        archive.verify_document_archive(other_doc)

        self.client.force_authenticate(self.user)
        response = self.client.get("/api/documents/evidence-status/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["documents"], 1)
        self.assertEqual(response.data["summary"]["evidence_ok"], 1)

    def test_evidence_report_verifies_document_and_audits_access(self):
        archive.verify_document_archive(self.doc)
        self.client.force_authenticate(self.user)

        response = self.client.get(f"/api/documents/{self.doc.id}/evidence/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ok")
        self.assertTrue(response.data["integrity"]["chain_ok"])
        self.assertEqual(response.data["versions"][0]["version_no"], 1)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="evidence_report_view",
                object_type="Document",
                object_id=str(self.doc.id),
            ).exists()
        )

    def test_evidence_report_is_owner_scoped(self):
        other = User.objects.create_user(username="other-owner", password="pw", role="user")
        self.client.force_authenticate(other)

        response = self.client.get(f"/api/documents/{self.doc.id}/evidence/")

        self.assertEqual(response.status_code, 404)
