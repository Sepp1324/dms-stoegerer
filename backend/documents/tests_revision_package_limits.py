"""P2: Der synchrone Revisionspaket-Export ist gegen sehr große Pakete begrenzt."""
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import Document, DocumentVersion

User = get_user_model()


class RevisionPackageLimitTests(APITestCase):
    def setUp(self):
        cache.clear()  # Throttle-Zähler isolieren
        self.user = User.objects.create_user("rev", password="pw", role="user")
        self.doc = Document.objects.create(title="Export", owner=self.user)
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"%PDF-1.4 einige bytes")
        tmp.close()
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.remove(tmp.name))
        self.version = DocumentVersion.objects.create(
            document=self.doc, version_no=1, file_path=tmp.name, sha256="a" * 64
        )
        self.doc.current_version = self.version
        self.doc.save(update_fields=["current_version"])
        self.client.force_authenticate(self.user)

    @override_settings(REVISION_PACKAGE_MAX_MB=0)
    def test_zu_grosses_paket_413(self):
        resp = self.client.get(f"/api/documents/{self.doc.id}/revision-package/")
        self.assertEqual(resp.status_code, 413)

    @override_settings(REVISION_PACKAGE_MAX_VERSIONS=0)
    def test_zu_viele_versionen_413(self):
        resp = self.client.get(f"/api/documents/{self.doc.id}/revision-package/")
        self.assertEqual(resp.status_code, 413)
