"""Tests: KI-Endpunkte (Copilot/semantische Suche) sind rate-limitiert (P2).

Bremst Provider-Kosten und CPU/RAM-Last. Muster wie tests_upload_throttle:
``patch.dict`` auf das beim Import gebundene THROTTLE_RATES-Dict.
"""
from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .throttling import AiRateThrottle

LOW_RATES = {"ai": "2/min"}


@mock.patch.dict(AiRateThrottle.THROTTLE_RATES, LOW_RATES)
class AiThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        User = get_user_model()
        self.user = User.objects.create_user(username="ai", password="pw12345!")
        self.client.force_authenticate(self.user)

    def test_ask_wird_nach_limit_gedrosselt(self):
        url = reverse("ask")
        # Throttle greift in initial() VOR dem Handler – auch der 400 (zu kurze
        # Frage) verbraucht Budget. Bei 2/min ist der dritte Request 429.
        codes = [self.client.post(url, {"question": "x"}, format="json").status_code for _ in range(3)]
        self.assertNotIn(429, codes[:2])
        self.assertEqual(codes[2], 429)

    def test_semantische_suche_wird_nach_limit_gedrosselt(self):
        url = reverse("search-semantic")
        codes = [self.client.get(url, {"q": "x"}).status_code for _ in range(3)]
        self.assertNotIn(429, codes[:2])
        self.assertEqual(codes[2], 429)

    def test_getrennte_nutzer_getrennte_budgets(self):
        User = get_user_model()
        other = User.objects.create_user(username="ai2", password="pw12345!")
        url = reverse("ask")
        for _ in range(2):
            self.client.post(url, {"question": "x"}, format="json")
        # Erschöpft für user:
        self.assertEqual(self.client.post(url, {"question": "x"}, format="json").status_code, 429)
        # Anderer Nutzer hat eigenes Budget:
        self.client.force_authenticate(other)
        self.assertNotEqual(self.client.post(url, {"question": "x"}, format="json").status_code, 429)
