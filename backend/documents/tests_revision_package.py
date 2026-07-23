import hashlib
import json
import os
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

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

    def test_build_failure_raeumt_tempdatei_auf(self):
        # Reißt der ZIP-Aufbau nach mkstemp ab, darf keine verwaiste dms-revpkg-*.zip
        # im Temp-Verzeichnis zurückbleiben.
        user = User.objects.create_user(username="rev-fail", password="pw", role="user")
        doc, _version = self.make_document(owner=user)

        tmp_root = tempfile.gettempdir()

        def _revpkgs():
            return {n for n in os.listdir(tmp_root) if n.startswith("dms-revpkg-")}

        before = _revpkgs()
        with mock.patch.object(
            revision_package, "_fill_revision_zip", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                revision_package.build_document_revision_package(doc)
        self.assertEqual(_revpkgs() - before, set())  # kein Leak


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

        payload = b"".join(response.streaming_content)  # FileResponse -> Streaming
        with zipfile.ZipFile(BytesIO(payload)) as zf:
            audit = json.loads(zf.read("audit.json"))
        # Der Export-Audit wird bewusst ERST NACH erfolgreichem Build geschrieben
        # (kein "Export" protokollieren, der scheitern könnte) und ist daher NICHT
        # im Paket selbst enthalten – er liegt aber in der DB (oben geprüft).
        self.assertFalse(
            any(entry["action"] == "revision_package_export" for entry in audit)
        )

    def test_revision_package_respects_owner_scope(self):
        self.client.force_authenticate(self.other)

        response = self.client.get(f"/api/documents/{self.doc.id}/revision-package/")

        self.assertEqual(response.status_code, 404)

    def test_fehlgeschlagener_export_schreibt_keinen_audit(self):
        # Scheitert der Build, darf KEIN "revision_package_export" protokolliert
        # werden (Audit erst nach erfolgreichem Export).
        self.client.force_authenticate(self.user)
        with mock.patch.object(
            revision_package, "_fill_revision_zip", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                self.client.get(f"/api/documents/{self.doc.id}/revision-package/")
        self.assertFalse(
            AuditLogEntry.objects.filter(
                action="revision_package_export",
                object_id=str(self.doc.id),
            ).exists()
        )
