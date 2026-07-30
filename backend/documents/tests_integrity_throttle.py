"""P2: Integritäts-/Evidence-Endpunkte lesen+hashen synchron alle Dateien und
sind daher – wie der Revisionspaket-Export – frequenzgedrosselt, damit wenige
große Requests die Webworker nicht blockieren."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase

from .models import Document, DocumentVersion
from .throttling import IntegrityCheckRateThrottle

User = get_user_model()


class IntegrityThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()  # Throttle-Historie je Test frisch
        self.user = User.objects.create_user("ith", password="pw", role="user")
        self.doc = Document.objects.create(title="D", owner=self.user)
        DocumentVersion.objects.create(
            document=self.doc, version_no=1, file_path="/tmp/none.pdf", sha256="a" * 64
        )
        self.client.force_authenticate(self.user)

    def test_integrity_wird_gedrosselt(self):
        with mock.patch.object(
            IntegrityCheckRateThrottle, "get_rate", return_value="1/minute"
        ):
            r1 = self.client.get(f"/api/documents/{self.doc.id}/integrity/")
            r2 = self.client.get(f"/api/documents/{self.doc.id}/integrity/")
        self.assertEqual(r1.status_code, 200, r1.content)
        self.assertEqual(r2.status_code, 429)  # zweiter Request gedrosselt

    def test_evidence_wird_gedrosselt(self):
        with mock.patch.object(
            IntegrityCheckRateThrottle, "get_rate", return_value="1/minute"
        ):
            r1 = self.client.get(f"/api/documents/{self.doc.id}/evidence/")
            r2 = self.client.get(f"/api/documents/{self.doc.id}/evidence/")
        self.assertEqual(r1.status_code, 200, r1.content)
        self.assertEqual(r2.status_code, 429)
