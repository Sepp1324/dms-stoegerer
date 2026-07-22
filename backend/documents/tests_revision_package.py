import hashlib
import json
import os
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from .models import AuditLogEntry, Document, DocumentVersion
from .services import revision_package, version_snapshot

User = get_user_model()


class RevisionPackageMixin:
    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()
        super().tearDown()

    def make_document(self, owner=None):
        doc = Document.objects.create(title="Steuer Rechnung 2026", owner=owner)
        path = Path(self.tmpdir.name) / "rechnung.pdf"
        content = b"%PDF revision package test"
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
        self.addCleanup(lambda: os.path.exists(package.path) and os.unlink(package.path))

        with zipfile.ZipFile(package.path) as zf:
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
        # Export-Audit wird NUR nach erfolgreichem Build geschrieben (in der DB).
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="revision_package_export",
                object_type="Document",
                object_id=str(self.doc.id),
            ).exists()
        )
        # Das Paket ist ein gültiges ZIP mit audit.json (das Export-Ereignis selbst
        # steht bewusst NICHT drin – es entsteht erst nach dem Build).
        payload = b"".join(response.streaming_content)  # FileResponse -> Streaming
        with zipfile.ZipFile(BytesIO(payload)) as zf:
            self.assertIn("audit.json", zf.namelist())

    def test_build_fehler_raeumt_temp_zip_auf_und_kein_audit(self):
        import glob
        import tempfile
        from unittest import mock

        before = set(glob.glob(f"{tempfile.gettempdir()}/dms-revpkg-*.zip"))
        with mock.patch(
            "documents.services.revision_package._write_json",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                revision_package.build_document_revision_package(self.doc)
        after = set(glob.glob(f"{tempfile.gettempdir()}/dms-revpkg-*.zip"))
        self.assertEqual(before, after, "verwaiste Temp-ZIP nach Fehler")

    def test_revision_package_respects_owner_scope(self):
        self.client.force_authenticate(self.other)

        response = self.client.get(f"/api/documents/{self.doc.id}/revision-package/")

        self.assertEqual(response.status_code, 404)
