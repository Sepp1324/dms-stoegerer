"""Tests für die Mobile-Erfassung (STOAA-513 / AK 1–3 von STOAA-512).

Deckt ab: mehrere Bilder → ein PDF in Reihenfolge, HEIC-Normalisierung,
Owner-/``ingest_source``-Zuordnung sowie die Fehlerpfade (Gast → 403,
fehlendes ``images`` → 400, Nicht-Bild → 400).

``process_document_version.delay`` wird gemockt und ``ORIGINALS_DIR`` auf ein
Temp-Verzeichnis umgebogen, damit die Tests ohne Celery-Broker/OCR und ohne
Schreibzugriff auf das echte Datenverzeichnis laufen.
"""
import io
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from PIL import Image
from rest_framework.test import APITestCase

from . import storage, tasks
from .models import Document

User = get_user_model()


class MobileCaptureUploadTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="mobiluser", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="mobilgast", password="pw", role="guest"
        )

    def setUp(self):
        self.url = reverse("document-mobile-capture")

        # Uploads in ein Temp-Verzeichnis lenken (kein echtes Datenverzeichnis).
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        originals = Path(self._tmp.name) / "originals"
        originals.mkdir(parents=True, exist_ok=True)
        p_orig = mock.patch.object(storage, "ORIGINALS_DIR", originals)
        p_orig.start()
        self.addCleanup(p_orig.stop)

        # Pipeline-Trigger abfangen (kein Broker/OCR im Test).
        p_delay = mock.patch.object(tasks.process_document_version, "delay")
        self.delay = p_delay.start()
        self.addCleanup(p_delay.stop)

    # -- Hilfsfunktionen -------------------------------------------------

    def _jpeg_bytes(self, color, size=(80, 100)):
        buf = io.BytesIO()
        Image.new("RGB", size, color).save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    def _jpeg(self, name, color):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(
            name, self._jpeg_bytes(color), content_type="image/jpeg"
        )

    def _heic(self, name="foto.heic", color="blue"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        import pillow_heif

        pillow_heif.register_heif_opener()
        buf = io.BytesIO()
        Image.new("RGB", (80, 100), color).save(buf, format="HEIF")
        return SimpleUploadedFile(name, buf.getvalue(), content_type="image/heic")

    def _page_count(self, document):
        import pikepdf

        with pikepdf.open(document.current_version.file_path) as pdf:
            return len(pdf.pages)

    # -- Tests -----------------------------------------------------------

    def test_drei_jpeg_ergeben_ein_pdf_mit_drei_seiten(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url,
            {
                "images": [
                    self._jpeg("s1.jpg", "red"),
                    self._jpeg("s2.jpg", "green"),
                    self._jpeg("s3.jpg", "blue"),
                ]
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Document.objects.count(), 1)
        doc = Document.objects.get()
        # owner = eingeloggter Nutzer, ingest_source == "mobile".
        self.assertEqual(doc.owner, self.user)
        self.assertEqual(doc.current_version.ingest_source, "mobile")
        self.assertEqual(doc.current_version.mime_type, "application/pdf")
        # Ein PDF mit exakt drei Seiten (Reihenfolge = Request-Reihenfolge).
        self.assertEqual(self._page_count(doc), 3)
        # Pipeline asynchron angestoßen.
        self.delay.assert_called_once()

    def test_heic_wird_zu_einseitigem_pdf(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url, {"images": [self._heic()]}, format="multipart"
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        doc = Document.objects.get()
        self.assertEqual(doc.current_version.ingest_source, "mobile")
        self.assertEqual(self._page_count(doc), 1)
        self.delay.assert_called_once()

    def test_eigener_titel_wird_uebernommen(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url,
            {"images": [self._jpeg("s1.jpg", "red")], "title": "Tankbeleg Juli"},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Document.objects.get().title, "Tankbeleg Juli")

    def test_gast_bekommt_403(self):
        self.client.force_authenticate(self.guest)
        resp = self.client.post(
            self.url, {"images": [self._jpeg("s1.jpg", "red")]}, format="multipart"
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Document.objects.count(), 0)
        self.delay.assert_not_called()

    def test_ohne_images_400(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Document.objects.count(), 0)
        self.delay.assert_not_called()

    def test_nicht_bild_datei_400(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_authenticate(self.user)
        txt = SimpleUploadedFile(
            "notiz.txt", b"kein bild sondern text", content_type="text/plain"
        )
        resp = self.client.post(self.url, {"images": [txt]}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Document.objects.count(), 0)
        self.delay.assert_not_called()

    def test_anonym_bekommt_401(self):
        resp = self.client.post(
            self.url, {"images": [self._jpeg("s1.jpg", "red")]}, format="multipart"
        )
        self.assertIn(resp.status_code, (401, 403))
        self.assertEqual(Document.objects.count(), 0)
