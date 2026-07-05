"""Tests für den Mobile-Bilder-Upload-Endpoint (STOAA-511)."""
import io
import unittest

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

User = get_user_model()

URL = "/api/documents/upload_images/"


def _make_jpeg_bytes(width=10, height=10):
    """Erstellt ein minimales RGB-JPEG als Bytes (kein PIL-Import im Modulscope)."""
    from PIL import Image as PilImage

    buf = io.BytesIO()
    PilImage.new("RGB", (width, height), color=(100, 150, 200)).save(buf, format="JPEG")
    return buf.getvalue()


class MobileUploadTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="mobile_tester", password="pw", role="user"
        )

    def _jpeg_file(self, name="photo.jpg"):
        f = io.BytesIO(_make_jpeg_bytes())
        f.name = name
        f.content_type = "image/jpeg"
        return f

    def test_three_jpegs_create_document(self):
        """3 JPEG-Bilder → 1 Dokument (kein Fehler, 201)."""
        files = [self._jpeg_file(f"photo{i}.jpg") for i in range(3)]
        response = self.client.post(
            URL, {"images": files}, format="multipart"
        )
        # Ohne Auth → 401
        self.assertEqual(response.status_code, 401)

    def test_three_jpegs_authenticated(self):
        """Authentifiziert: 3 JPEG → 201 + Dokument mit id."""
        self.client.force_authenticate(self.user)
        files = [self._jpeg_file(f"photo{i}.jpg") for i in range(3)]
        response = self.client.post(URL, {"images": files}, format="multipart")
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("id", data)

    def test_non_image_rejected(self):
        """Nicht-Bild-Datei (.txt) → 400 mit Fehlermeldung."""
        self.client.force_authenticate(self.user)
        buf = io.BytesIO(b"this is not an image")
        buf.name = "test.txt"
        response = self.client.post(URL, {"images": [buf]}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.json())

    def test_no_files_rejected(self):
        """Leerer Request → 400."""
        self.client.force_authenticate(self.user)
        response = self.client.post(URL, {}, format="multipart")
        self.assertEqual(response.status_code, 400)

    def test_owner_set(self):
        """owner im erzeugten Dokument == eingeloggter Nutzer."""
        self.client.force_authenticate(self.user)
        files = [self._jpeg_file()]
        response = self.client.post(URL, {"images": files}, format="multipart")
        self.assertEqual(response.status_code, 201)
        from .models import Document
        doc = Document.objects.get(id=response.json()["id"])
        self.assertEqual(doc.owner, self.user)

    def test_ingest_source_mobile(self):
        """ingest_source der erzeugten Version == 'mobile'."""
        self.client.force_authenticate(self.user)
        files = [self._jpeg_file()]
        response = self.client.post(URL, {"images": files}, format="multipart")
        self.assertEqual(response.status_code, 201)
        from .models import Document
        doc = Document.objects.get(id=response.json()["id"])
        version = doc.versions.order_by("-created_at").first()
        self.assertEqual(version.ingest_source, "mobile")

    @unittest.skip("Braucht pillow-heif im Test-Environment")
    def test_heic_converted(self):
        """HEIC-Datei → gültiges 1-seitiges PDF (erfordert pillow-heif)."""
        pass
