"""Tests für die read-only Nutzer-Auswahlliste (STOAA-221)."""
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

User = get_user_model()


class UserListEndpointTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="admin", password="pw", role="admin", email="admin@example.com"
        )
        cls.user = User.objects.create_user(
            username="berta", password="pw", role="user", email="berta@example.com"
        )
        cls.guest = User.objects.create_user(
            username="gast", password="pw", role="guest"
        )
        cls.inactive = User.objects.create_user(
            username="ehemalig", password="pw", role="user", is_active=False
        )
        cls.url = reverse("user-list")

    def test_requires_authentication(self):
        resp = self.client.get(self.url)
        self.assertIn(resp.status_code, (401, 403))

    def test_non_admin_forbidden(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_admin_gets_active_users(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        usernames = [u["username"] for u in resp.data]
        # Aktive Nutzer enthalten, Inaktive ausgeschlossen.
        self.assertIn("admin", usernames)
        self.assertIn("berta", usernames)
        self.assertIn("gast", usernames)
        self.assertNotIn("ehemalig", usernames)

    def test_sorted_by_username(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.url)
        usernames = [u["username"] for u in resp.data]
        self.assertEqual(usernames, sorted(usernames))

    def test_slim_payload_no_sensitive_fields(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.url)
        entry = next(u for u in resp.data if u["username"] == "berta")
        self.assertEqual(set(entry.keys()), {"id", "username", "email"})
        self.assertEqual(entry["email"], "berta@example.com")
        # Keine Rollen-/Rechte-/Passwortfelder werden geleakt.
        for leaked in ("role", "password", "is_dms_admin", "can_write", "is_superuser"):
            self.assertNotIn(leaked, entry)

    def test_not_paginated(self):
        """Antwort ist eine reine Liste (kein DRF-Pagination-Envelope)."""
        self.client.force_authenticate(self.admin)
        resp = self.client.get(self.url)
        self.assertIsInstance(resp.data, list)
