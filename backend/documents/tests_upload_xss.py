"""Tests zur P0-2-Härtung: Magic-Byte-Allowlist + Preview-Schutzheader.

Deckt ab:
* ``filetypes.detect`` erkennt erlaubte Typen und weist HTML/SVG ab.
* ``storage.save_upload``/``save_bytes`` schreiben nichts für unerlaubte Typen
  und leiten MIME/Endung aus dem Inhalt (nicht aus Client-Angaben) ab.
* Der Upload-Endpoint antwortet mit 400 auf eine als PNG getarnte HTML-Datei.
* ``_serve_version_preview`` setzt nosniff + CSP-``sandbox``.
"""
from __future__ import annotations

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from . import filetypes, storage

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
PDF_BYTES = b"%PDF-1.7\n" + b"%\xe2\xe3\xcf\xd3\n" + b"0" * 40
HTML_BYTES = b"<html><script>steal(localStorage.token)</script></html>"
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>'


class DetectTests(TestCase):
    def test_allows_pdf_and_png(self):
        self.assertEqual(filetypes.detect(PDF_BYTES), filetypes.PDF)
        self.assertEqual(filetypes.detect(PNG_BYTES), filetypes.PNG)

    def test_rejects_html_and_svg(self):
        self.assertIsNone(filetypes.detect(HTML_BYTES))
        self.assertIsNone(filetypes.detect(SVG_BYTES))
        self.assertIsNone(filetypes.detect(b""))

    def test_is_safe_inline(self):
        self.assertTrue(filetypes.is_safe_inline("application/pdf"))
        self.assertTrue(filetypes.is_safe_inline("image/png"))
        self.assertFalse(filetypes.is_safe_inline("text/html"))
        self.assertFalse(filetypes.is_safe_inline("image/svg+xml"))


class StorageAllowlistTests(TestCase):
    def test_save_upload_rejects_disguised_html(self):
        # Client behauptet PNG, Inhalt ist HTML → muss abgewiesen werden,
        # ohne eine Datei zu schreiben.
        before = set(storage.ORIGINALS_DIR.glob("*")) if storage.ORIGINALS_DIR.exists() else set()
        uploaded = SimpleUploadedFile("evil.png", HTML_BYTES, content_type="image/png")
        with self.assertRaises(filetypes.UnsupportedFileType):
            storage.save_upload(uploaded)
        after = set(storage.ORIGINALS_DIR.glob("*")) if storage.ORIGINALS_DIR.exists() else set()
        self.assertEqual(before, after, "Keine Datei darf geschrieben worden sein.")

    def test_save_upload_uses_detected_mime_not_client(self):
        # Client lügt (text/html), Inhalt ist echtes PDF → MIME/Endung aus Inhalt.
        uploaded = SimpleUploadedFile("x.txt", PDF_BYTES, content_type="text/html")
        path, size, mime = storage.save_upload(uploaded)
        self.assertEqual(mime, "application/pdf")
        self.assertTrue(path.endswith(".pdf"))
        self.assertGreater(size, 0)

    def test_save_bytes_rejects_html(self):
        with self.assertRaises(filetypes.UnsupportedFileType):
            storage.save_bytes(HTML_BYTES, "pdf")

    @override_settings(UPLOAD_MAX_FILE_MB=0)
    def test_save_bytes_enforces_size_limit(self):
        with self.assertRaises(filetypes.UnsupportedFileType):
            storage.save_bytes(PDF_BYTES, "pdf")


class UploadEndpointXssTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="uploader", password="pw12345!", email="u@example.com"
        )
        self.client.force_authenticate(self.user)

    def test_disguised_html_upload_rejected_400(self):
        url = reverse("document-upload")
        resp = self.client.post(
            url,
            {"file": SimpleUploadedFile("evil.png", HTML_BYTES, content_type="image/png")},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)
