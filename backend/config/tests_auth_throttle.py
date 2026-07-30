"""P1: JWT-Login/Refresh sind gegen Brute-Force gedrosselt."""
from unittest import mock

from django.core.cache import cache
from rest_framework.test import APITestCase

from documents.throttling import LoginRateThrottle, TokenRefreshRateThrottle


class AuthThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()  # Throttle-Historie je Test frisch

    def test_login_wird_gedrosselt(self):
        with mock.patch.object(LoginRateThrottle, "get_rate", return_value="1/minute"):
            r1 = self.client.post(
                "/api/auth/token/", {"username": "x", "password": "y"}
            )
            r2 = self.client.post(
                "/api/auth/token/", {"username": "x", "password": "y"}
            )
        self.assertNotEqual(r1.status_code, 429)  # erster Versuch (401)
        self.assertEqual(r2.status_code, 429)     # zweiter gedrosselt

    def test_refresh_wird_gedrosselt(self):
        with mock.patch.object(
            TokenRefreshRateThrottle, "get_rate", return_value="1/minute"
        ):
            r1 = self.client.post("/api/auth/token/refresh/", {"refresh": "invalid"})
            r2 = self.client.post("/api/auth/token/refresh/", {"refresh": "invalid"})
        self.assertNotEqual(r1.status_code, 429)
        self.assertEqual(r2.status_code, 429)
