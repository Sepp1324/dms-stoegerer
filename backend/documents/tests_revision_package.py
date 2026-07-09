import hashlib
import json
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from .models import AuditLogEntry, CaseFile, Document, DocumentVersion
from .services import revision_package, version_snapshot

User = get_user_model()


class RevisionPackageMixin:
    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()
        super().tearDown()

    def make_document(self, owner=None, *, title="Steuer Rechnung 2026", content=None):
        doc = Document.objects.create(title=title, owner=owner)
        path = Path(self.tmpdir.name) / f"rechnung-{doc.id}.pdf"
        content = content or f"%PDF revision package test {doc.id}".encode("utf-8")
        path.write_bytes(content)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=str(path),
            sha256=hashlib.sha256(content).hexdigest(),
            processing_state=DocumentVersion.ProcessingState.READY,
            is_immutable=False,
            ocr_text="Rechnung 2026 fuer Steuerberater",
            mime_type="application/pdf",
            size=len(content),
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        version_snapshot.write_snapshot_on_seal(version, actor=owner)
        DocumentVersion.objects.filter(pk=version.pk).update(is_immutable=True)
        version.refresh_from_db()
        doc.refresh_from_db()
        return doc, version


class RevisionPackageServiceTests(RevisionPackageMixin, TestCase):
    def test_build_package_contains_files_metadata_audit_and_manifest(self):
        user = User.objects.create_user(username="rev-user", password="pw", role="user")
        doc, version = self.make_document(owner=user)
        AuditLogEntry.objects.create(
            actor=user,
            action="update",
            object_type="Document",
            object_id=str(doc.id),
            detail={"field": "title"},
        )

        package = revision_package.build_document_revision_package(doc)

        with zipfile.ZipFile(BytesIO(package.content)) as zf:
            names = set(zf.namelist())
            self.assertIn("metadata.json", names)
            self.assertIn("integrity.json", names)
            self.assertIn("retention.json", names)
            self.assertIn("audit.json", names)
            self.assertIn("manifest.json", names)
            self.assertIn("files/v1/original.pdf", names)
            self.assertIn("text/v1-ocr.txt", names)
            self.assertIn("snapshots/v1-metadata_snapshot.json", names)

            manifest = json.loads(zf.read("manifest.json"))
            metadata = json.loads(zf.read("metadata.json"))
            audit = json.loads(zf.read("audit.json"))

        self.assertEqual(manifest["document"]["id"], doc.id)
        self.assertEqual(manifest["archive_status"], Document.ArchiveStatus.OK)
        self.assertEqual(metadata["versions"][0]["seal_hash"], version.seal_hash)
        self.assertTrue(any(entry["action"] == "update" for entry in audit))

    def test_build_case_file_package_contains_nested_document_packages(self):
        user = User.objects.create_user(username="case-rev-user", password="pw", role="user")
        case_file = CaseFile.objects.create(
            title="Steuerakte 2026",
            description="Alles fuer den Steuerberater",
            owner=user,
        )
        first, _v1 = self.make_document(owner=user, title="Rechnung A")
        second, _v2 = self.make_document(owner=user, title="Rechnung B")
        Document.objects.filter(pk__in=[first.pk, second.pk]).update(case_file=case_file)
        AuditLogEntry.objects.create(
            actor=user,
            action="case_file_update",
            object_type="CaseFile",
            object_id=str(case_file.id),
            detail={"status": "active"},
        )

        package = revision_package.build_case_file_revision_package(case_file)

        with zipfile.ZipFile(BytesIO(package.content)) as zf:
            names = set(zf.namelist())
            self.assertIn("casefile-metadata.json", names)
            self.assertIn("audit.json", names)
            self.assertIn("manifest.json", names)
            nested = [name for name in names if name.startswith("documents/") and name.endswith(".zip")]
            self.assertEqual(len(nested), 2)

            manifest = json.loads(zf.read("manifest.json"))
            metadata = json.loads(zf.read("casefile-metadata.json"))
            audit = json.loads(zf.read("audit.json"))

        self.assertEqual(manifest["case_file"]["id"], case_file.id)
        self.assertEqual(manifest["document_count"], 2)
        self.assertEqual(len(metadata["documents"]), 2)
        self.assertTrue(any(entry["action"] == "case_file_update" for entry in audit))


class RevisionPackageApiTests(RevisionPackageMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="rev-api", password="pw", role="user")
        self.other = User.objects.create_user(username="rev-other", password="pw", role="user")
        self.doc, _version = self.make_document(owner=self.user)

    def test_revision_package_endpoint_returns_zip_and_audits_export(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(f"/api/documents/{self.doc.id}/revision-package/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("revisionspaket.zip", response["Content-Disposition"])
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="revision_package_export",
                object_type="Document",
                object_id=str(self.doc.id),
            ).exists()
        )

        with zipfile.ZipFile(BytesIO(response.content)) as zf:
            audit = json.loads(zf.read("audit.json"))
        self.assertTrue(
            any(entry["action"] == "revision_package_export" for entry in audit)
        )

    def test_revision_package_respects_owner_scope(self):
        self.client.force_authenticate(self.other)

        response = self.client.get(f"/api/documents/{self.doc.id}/revision-package/")

        self.assertEqual(response.status_code, 404)

    def test_case_file_revision_package_endpoint_returns_nested_zip_and_audits(self):
        case_file = CaseFile.objects.create(title="Steuerakte", owner=self.user)
        Document.objects.filter(pk=self.doc.pk).update(case_file=case_file)
        self.client.force_authenticate(self.user)

        response = self.client.get(f"/api/case-files/{case_file.id}/revision-package/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("revisionspaket.zip", response["Content-Disposition"])
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="case_file_revision_package_export",
                object_type="CaseFile",
                object_id=str(case_file.id),
            ).exists()
        )

        with zipfile.ZipFile(BytesIO(response.content)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            audit = json.loads(zf.read("audit.json"))
            nested = [name for name in zf.namelist() if name.startswith("documents/")]

        self.assertEqual(manifest["case_file"]["id"], case_file.id)
        self.assertEqual(manifest["document_count"], 1)
        self.assertEqual(len(nested), 1)
        self.assertTrue(
            any(entry["action"] == "case_file_revision_package_export" for entry in audit)
        )

    def test_case_file_revision_package_respects_owner_scope(self):
        case_file = CaseFile.objects.create(title="Fremde Akte", owner=self.user)
        self.client.force_authenticate(self.other)

        response = self.client.get(f"/api/case-files/{case_file.id}/revision-package/")

        self.assertEqual(response.status_code, 404)
