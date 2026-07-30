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

    def test_cache_ausfall_bricht_login_nicht_mit_500(self):
        """P1: Ist der Throttle-Cache (Redis) nicht erreichbar, wirft die Basisklasse
        einen ConnectionError. Das darf den Login NICHT mit 500 killen – das Throttle
        lässt den Request durch (fail-open), die Auth läuft normal weiter (401)."""
        with mock.patch(
            "rest_framework.throttling.SimpleRateThrottle.allow_request",
            side_effect=ConnectionError("redis weg"),
        ):
            resp = self.client.post(
                "/api/auth/token/", {"username": "x", "password": "y"}
            )
        self.assertNotEqual(resp.status_code, 500)  # KEIN 500 bei Cache-Ausfall
        self.assertEqual(resp.status_code, 401)      # Auth prüft normal weiter

    def test_num_proxies_konfiguriert(self):
        # P2: Ohne NUM_PROXIES wäre das IP-Throttle über X-Forwarded-For umgehbar.
        from django.conf import settings

        self.assertIn("NUM_PROXIES", settings.REST_FRAMEWORK)
        self.assertGreaterEqual(settings.REST_FRAMEWORK["NUM_PROXIES"], 1)
