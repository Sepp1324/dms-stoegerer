"""Tests: Thumbnail-Endpunkt hat HTTP-Caching (ETag + Cache-Control + 304)."""
from __future__ import annotations

import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Document, DocumentVersion


class ThumbnailCacheTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.thumb = os.path.join(self.tmp, "t.jpg")
        with open(self.thumb, "wb") as fh:
            fh.write(b"\xff\xd8\xff" + b"0" * 32)  # minimaler JPEG-Header
        self.user = get_user_model().objects.create_user("tc", password="pw12345!")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.doc = Document.objects.create(title="D", owner=self.user)
        self.version = DocumentVersion.objects.create(
            document=self.doc,
            version_no=1,
            file_path="/tmp/x",
            sha256="abc123",
            thumbnail_path=self.thumb,
        )
        self.doc.current_version = self.version
        self.doc.save(update_fields=["current_version"])
        self.url = f"/api/documents/{self.doc.id}/thumbnail/"

    def test_sets_etag_and_cache_control(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp["ETag"])
        self.assertIn("max-age", resp["Cache-Control"])
        self.assertIn("private", resp["Cache-Control"])

    def test_conditional_request_returns_304(self):
        first = self.client.get(self.url)
        etag = first["ETag"]
        again = self.client.get(self.url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again["ETag"], etag)

    def test_etag_changes_when_version_changes(self):
        etag_v1 = self.client.get(self.url)["ETag"]
        # Neue aktuelle Version -> anderer ETag (kein veraltetes Thumbnail).
        v2 = DocumentVersion.objects.create(
            document=self.doc,
            version_no=2,
            file_path="/tmp/y",
            sha256="def456",
            thumbnail_path=self.thumb,
        )
        self.doc.current_version = v2
        self.doc.save(update_fields=["current_version"])
        etag_v2 = self.client.get(self.url)["ETag"]
        self.assertNotEqual(etag_v1, etag_v2)
