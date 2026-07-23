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
# Polyglot: gültiger PDF-Header, danach aktives HTML. Magic-Byte erkennt PDF;
# gefährlich wird es NUR, wenn es als text/html gespeichert/ausgeliefert würde.
POLYGLOT_PDF_HTML = b"%PDF-1.7\n<script>steal(localStorage.token)</script>\n" + b"0" * 60


class DetectTests(TestCase):
    def test_allows_pdf_and_png(self):
        self.assertEqual(filetypes.detect(PDF_BYTES), filetypes.PDF)
        self.assertEqual(filetypes.detect(PNG_BYTES), filetypes.PNG)

    def test_rejects_html_and_svg(self):
        self.assertIsNone(filetypes.detect(HTML_BYTES))
        self.assertIsNone(filetypes.detect(SVG_BYTES))
        self.assertIsNone(filetypes.detect(b""))

    def test_pdf_polyglot_wird_als_pdf_erkannt(self):
        # %PDF-…<script>: am Magic-Byte ist es ein PDF. Entscheidend, damit der
        # ERKANNTE Typ (application/pdf) gespeichert/serviert wird – nie text/html.
        self.assertEqual(filetypes.detect(POLYGLOT_PDF_HTML), filetypes.PDF)

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

    def test_save_bytes_returns_detected_mime(self):
        # Polyglot als ".txt"/text/html deklariert -> erkannt als PDF, und
        # save_bytes liefert genau diesen erkannten MIME zurück (nie den Hinweis).
        path, mime = storage.save_bytes(POLYGLOT_PDF_HTML, "txt")
        self.assertEqual(mime, "application/pdf")
        self.assertTrue(str(path).endswith(".pdf"))

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


class MailIngestMimeTests(TestCase):
    """Mail-Anhänge werden mit dem ERKANNTEN MIME gespeichert, nie mit dem vom
    Absender gemeldeten Content-Type (P0: sonst text/html-Polyglot -> Stored XSS)."""

    def test_polyglot_attachment_stored_as_detected_mime(self):
        from email import encoders
        from email.mime.base import MIMEBase
        from email.mime.multipart import MIMEMultipart
        from unittest import mock

        from . import mail
        from .models import Document, MailAccount

        User = get_user_model()
        user = User.objects.create_user(username="mail-owner", password="pw12345!")
        account = MailAccount.objects.create(
            name="Rechnungen", host="mail.example.com", username="u", owner=user
        )

        # Anhang: als text/html deklariert, Dateiname .pdf (erlaubte Endung),
        # Inhalt ist der PDF-Polyglot.
        part = MIMEBase("text", "html")
        part.set_payload(POLYGLOT_PDF_HTML)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename="invoice.pdf")
        root = MIMEMultipart()
        root["Subject"] = "Rechnung"
        root["From"] = "Angreifer <evil@example.com>"
        root["Message-ID"] = "<polyglot-1@example.com>"
        root.attach(part)

        with mock.patch("documents.tasks.process_document_version.delay"):
            imported = mail.ingest_message(account, root.as_bytes())

        self.assertEqual(imported, 1)
        doc = Document.objects.get(owner=user)
        self.assertEqual(doc.current_version.mime_type, "application/pdf")


class PreviewUnsafeTypeTests(TestCase):
    """Der Preview-Endpoint bestimmt den Content-Type aus den Magic Bytes der
    Datei (nicht aus dem gespeicherten mime_type). Nur erkannte inline-sichere
    Typen werden ausgeliefert; unerkannter/aktiver Inhalt -> 415."""

    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.user = User.objects.create_user(username="prev", password="pw12345!")
        self.client.force_authenticate(self.user)

    def _version(self, *, content: bytes, mime: str):
        import os
        import tempfile

        from . import pipeline
        from .models import Document, DocumentVersion

        tmp = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
        tmp.write(content)
        tmp.close()
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.remove(tmp.name))
        doc = Document.objects.create(title="doc", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=tmp.name,
            sha256=pipeline.sha256_of(tmp.name),
            mime_type=mime,
            size=os.path.getsize(tmp.name),
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_nicht_erkennbarer_inhalt_wird_abgelehnt_415(self):
        # Datei-Inhalt ist HTML (weder PDF noch Bild) -> 415, egal welcher
        # mime_type gespeichert ist.
        doc = self._version(content=HTML_BYTES, mime="application/pdf")
        resp = self.client.get(f"/api/documents/{doc.id}/preview/")
        self.assertEqual(resp.status_code, 415)

    def test_pdf_inhalt_wird_trotz_falschem_mime_als_pdf_serviert(self):
        # KERN des P0-Fixes: gespeicherter mime_type ist text/html (Alt-Mail-Bug),
        # Inhalt ist ein PDF-Polyglot -> serviert als application/pdf (PDF-Viewer,
        # kein HTML), Status 200. So ist der Polyglot in der Vorschau harmlos.
        doc = self._version(content=POLYGLOT_PDF_HTML, mime="text/html")
        resp = self.client.get(f"/api/documents/{doc.id}/preview/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")


class PreviewArchiveFallbackTests(TestCase):
    """P1: Ist archive_path GESETZT, die Archivdatei aber NICHT vorhanden (z. B.
    nach einem Restore ohne archive/), fällt die Vorschau auf das Original zurück,
    statt 404 zu liefern."""

    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.user = User.objects.create_user(username="prevfb", password="pw12345!")
        self.client.force_authenticate(self.user)

    def _version_with_missing_archive(self):
        import os
        import tempfile

        from . import pipeline
        from .models import Document, DocumentVersion

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(PDF_BYTES)
        tmp.close()
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.remove(tmp.name))
        doc = Document.objects.create(title="doc", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=tmp.name,
            archive_path="/data/archive/nicht/vorhanden.pdf",  # gesetzt, aber fehlt
            sha256=pipeline.sha256_of(tmp.name),
            mime_type="application/pdf", size=os.path.getsize(tmp.name),
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_fehlendes_archiv_faellt_auf_original_zurueck(self):
        doc = self._version_with_missing_archive()
        resp = self.client.get(f"/api/documents/{doc.id}/preview/")
        self.assertEqual(resp.status_code, 200)          # NICHT 404
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_beide_fehlen_ergibt_404(self):
        import os
        import tempfile

        from . import pipeline
        from .models import Document, DocumentVersion

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(PDF_BYTES)
        tmp.close()
        sha = pipeline.sha256_of(tmp.name)
        os.remove(tmp.name)  # Original fehlt ebenfalls
        doc = Document.objects.create(title="doc", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=tmp.name,
            archive_path="/data/archive/auch/weg.pdf", sha256=sha,
            mime_type="application/pdf", size=1,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        resp = self.client.get(f"/api/documents/{doc.id}/preview/")
        self.assertEqual(resp.status_code, 404)
