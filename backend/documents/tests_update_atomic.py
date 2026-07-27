"""P1: Dokument-PATCH ist atomar mit Audit/Workflows/Review-Sync.

Scheitert ein nachgelagerter Schritt (Workflow-Engine, Review-Task-Sync), darf
KEINE Teiländerung dauerhaft bleiben und kein Audit-Eintrag zurückbleiben.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import AuditLogEntry, Document

User = get_user_model()


class DocumentUpdateAtomicTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("upd", password="pw", role="user")
        self.doc = Document.objects.create(title="Original", owner=self.user)
        self.client.force_authenticate(self.user)

    def test_fehler_nach_speichern_rollt_alles_zurueck(self):
        # Workflow-Engine wirft NACH dem Speichern -> die gesamte PATCH-Transaktion
        # muss zurückrollen: Titel unverändert, KEIN "update"-Audit.
        self.client.raise_request_exception = False
        with mock.patch(
            "documents.workflows.run_workflows", side_effect=RuntimeError("boom")
        ):
            resp = self.client.patch(
                f"/api/documents/{self.doc.id}/", {"title": "Geändert"}, format="json"
            )
        self.assertEqual(resp.status_code, 500)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.title, "Original")  # Speichern zurückgerollt
        self.assertFalse(
            AuditLogEntry.objects.filter(
                action="update", object_id=str(self.doc.id)
            ).exists()
        )

    def test_erfolgreicher_patch_schreibt_audit(self):
        resp = self.client.patch(
            f"/api/documents/{self.doc.id}/", {"title": "Neuer Titel"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.title, "Neuer Titel")
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="update", object_id=str(self.doc.id)
            ).exists()
        )
