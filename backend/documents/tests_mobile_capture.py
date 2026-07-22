"""Tests für die Mobile-Erfassung (STOAA-513).

Endpoint ``POST /api/documents/mobile-capture/``: mehrere Bilder (auch HEIC)
werden in Request-Reihenfolge zu EINEM PDF zusammengefügt und die bestehende
Pipeline angestoßen (``owner`` = Request-Nutzer, ``ingest_source`` = "mobile").

``process_document_version.delay`` ist gemockt (kein Celery/OCR nötig), und
``storage.ORIGINALS_DIR`` zeigt auf ein Temp-Verzeichnis.
"""
import io
import tempfile
from pathlib import Path
from unittest import mock

import pikepdf
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APITestCase

from . import storage, tasks
from .models import Document, DocumentVersion

User = get_user_model()

URL = "/api/documents/mobile-capture/"


def _jpeg(color=(200, 30, 30), size=(120, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _heic(color=(20, 120, 200), size=(120, 160)) -> bytes:
    import pillow_heif

    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="HEIF")
    return buf.getvalue()


def _file(data: bytes, name: str, content_type: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=content_type)


class MobileCaptureUploadTests(APITestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.originals = Path(self._tmp.name) / "originals"
        self.originals.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._tmp.cleanup)

        self.user = User.objects.create_user(
            username="mobi", password="pw", role="user", email="mobi@example.com"
        )
        self.guest = User.objects.create_user(
            username="gast", password="pw", role="guest"
        )

    def _post(self, data, *, user):
        """Sendet den multipart-Request mit gemocktem Worker + Temp-Originals."""
        self.client.force_authenticate(user)
        with mock.patch.object(storage, "ORIGINALS_DIR", self.originals), mock.patch.object(
            tasks.process_document_version, "delay"
        ) as delay:
            resp = self.client.post(URL, data, format="multipart")
        return resp, delay

    def _stored_pdf_bytes(self, document) -> bytes:
        version = document.current_version
        return Path(version.file_path).read_bytes()

    # --- Happy Path: mehrere JPEG → mehrseitiges PDF ---------------------
    def test_drei_jpeg_werden_ein_pdf_mit_drei_seiten(self):
        data = {
            "images": [
                _file(_jpeg((200, 30, 30)), "a.jpg", "image/jpeg"),
                _file(_jpeg((30, 200, 30)), "b.jpg", "image/jpeg"),
                _file(_jpeg((30, 30, 200)), "c.jpg", "image/jpeg"),
            ]
        }
        resp, delay = self._post(data, user=self.user)
        self.assertEqual(resp.status_code, 201, resp.data)

        document = Document.objects.get(pk=resp.data["id"])
        version = document.current_version
        # owner + ingest_source server-seitig gesetzt.
        self.assertEqual(document.owner, self.user)
        self.assertEqual(version.ingest_source, "mobile")
        self.assertEqual(version.mime_type, "application/pdf")
        delay.assert_called_once_with(version.id)

        with pikepdf.open(io.BytesIO(self._stored_pdf_bytes(document))) as pdf:
            self.assertEqual(len(pdf.pages), 3)

    # --- HEIC → gültiges 1-seitiges PDF ---------------------------------
    def test_ein_heic_wird_gueltiges_einseitiges_pdf(self):
        data = {"images": [_file(_heic(), "foto.heic", "image/heic")]}
        resp, delay = self._post(data, user=self.user)
        self.assertEqual(resp.status_code, 201, resp.data)

        document = Document.objects.get(pk=resp.data["id"])
        self.assertEqual(document.current_version.ingest_source, "mobile")
        with pikepdf.open(io.BytesIO(self._stored_pdf_bytes(document))) as pdf:
            self.assertEqual(len(pdf.pages), 1)

    # --- Titel-Default --------------------------------------------------
    def test_default_titel_wird_gesetzt(self):
        data = {"images": [_file(_jpeg(), "a.jpg", "image/jpeg")]}
        resp, _ = self._post(data, user=self.user)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data["title"].startswith("Mobile-Erfassung "))

    def test_expliziter_titel_wird_uebernommen(self):
        data = {
            "title": "Meine Belege",
            "images": [_file(_jpeg(), "a.jpg", "image/jpeg")],
        }
        resp, _ = self._post(data, user=self.user)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["title"], "Meine Belege")

    # --- Fehlerfälle ----------------------------------------------------
    def test_nicht_bild_datei_ergibt_400(self):
        data = {"images": [_file(b"kein bild, nur text", "notiz.txt", "text/plain")]}
        resp, delay = self._post(data, user=self.user)
        self.assertEqual(resp.status_code, 400, resp.data)
        delay.assert_not_called()
        self.assertEqual(Document.objects.count(), 0)

    def test_fehlendes_images_feld_ergibt_400(self):
        resp, delay = self._post({"title": "x"}, user=self.user)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("images", resp.data["detail"])
        delay.assert_not_called()

    def test_gast_ist_403(self):
        data = {"images": [_file(_jpeg(), "a.jpg", "image/jpeg")]}
        resp, delay = self._post(data, user=self.guest)
        self.assertEqual(resp.status_code, 403, resp.data)
        delay.assert_not_called()
        self.assertEqual(Document.objects.count(), 0)

    def test_zu_viele_bilder_ergibt_400(self):
        data = {"images": [_file(_jpeg(), f"{i}.jpg", "image/jpeg") for i in range(31)]}
        resp, delay = self._post(data, user=self.user)
        self.assertEqual(resp.status_code, 400, resp.data)
        delay.assert_not_called()
        self.assertEqual(Document.objects.count(), 0)

    # --- RAM-Schutz (P1) ------------------------------------------------
    @override_settings(MOBILE_CAPTURE_MAX_TOTAL_BYTES=100)  # winziges Gesamtlimit
    def test_gesamtlimit_ueberschritten_ergibt_400(self):
        # Zwei kleine Bilder überschreiten zusammen bereits 100 Bytes.
        data = {"images": [
            _file(_jpeg(), "a.jpg", "image/jpeg"),
            _file(_jpeg(), "b.jpg", "image/jpeg"),
        ]}
        resp, delay = self._post(data, user=self.user)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Gesamtlimit", resp.data["detail"])
        delay.assert_not_called()
        self.assertEqual(Document.objects.count(), 0)

    @override_settings(MOBILE_CAPTURE_MAX_IMAGE_PIXELS=100)  # 100 Pixel -> jedes Bild zu groß
    def test_pixel_bombe_wird_abgewiesen(self):
        data = {"images": [_file(_jpeg(size=(120, 160)), "gross.jpg", "image/jpeg")]}
        resp, delay = self._post(data, user=self.user)
        self.assertEqual(resp.status_code, 400, resp.data)
        delay.assert_not_called()
        self.assertEqual(Document.objects.count(), 0)
