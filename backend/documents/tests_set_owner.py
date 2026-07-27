"""P1: Admin-Triage kann einem eigentümerlosen Dokument einen Owner zuweisen.

Das Frontend ruft ``POST /documents/{id}/set-owner/`` – ohne diese Action lief
der Button ins 404. Hier: Route existiert, Admin weist zu (200 + Audit),
Normalnutzer wird abgewiesen (403), fehlerhafte Eingaben → 400.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import AuditLogEntry, Document

User = get_user_model()


class SetOwnerActionTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user("so-admin", password="pw", role="admin")
        self.user = User.objects.create_user("so-user", password="pw", role="user")
        # Triage-Dokument: bewusst ohne Owner.
        self.doc = Document.objects.create(title="Triage", owner=None)

    def test_admin_weist_owner_zu(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            f"/api/documents/{self.doc.id}/set-owner/",
            {"owner": self.user.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.owner_id, self.user.id)
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="set_owner", object_id=str(self.doc.id)
            ).exists()
        )

    def test_normalnutzer_darf_nicht(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            f"/api/documents/{self.doc.id}/set-owner/",
            {"owner": self.user.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.doc.refresh_from_db()
        self.assertIsNone(self.doc.owner_id)

    def test_fehlende_owner_id_400(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            f"/api/documents/{self.doc.id}/set-owner/", {}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_unbekannter_nutzer_400(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            f"/api/documents/{self.doc.id}/set-owner/",
            {"owner": 999999},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
