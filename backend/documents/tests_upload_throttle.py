"""Tests: Upload-Endpunkte sind rate-limitiert (P1, DoS-Schutz)."""
from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .throttling import UploadRateThrottle

# Enge Rate nur für den Test. Bewusst NICHT über override_settings(REST_FRAMEWORK):
# SimpleRateThrottle.THROTTLE_RATES ist ein beim Import gebundenes Klassenattribut
# (Referenz aufs api_settings-Dict) – override_settings erzeugt ein neues Dict,
# das die Drossel nie sieht. patch.dict mutiert genau das gelesene Dict.
LOW_RATES = {"upload": "2/min", "capture": "2/min"}


@mock.patch.dict(UploadRateThrottle.THROTTLE_RATES, LOW_RATES)
class UploadThrottleTests(TestCase):
    def setUp(self):
        cache.clear()  # Zähler zwischen Tests isolieren (LocMemCache ist prozessweit)
        self.client = APIClient()
        User = get_user_model()
        self.user = User.objects.create_user(username="up", password="pw12345!")
        self.client.force_authenticate(self.user)

    def test_upload_is_throttled_after_limit(self):
        url = reverse("document-upload")
        # Der Throttle greift in initial() VOR dem Handler – auch Requests, die
        # mangels Datei mit 400 enden, verbrauchen Budget. Bei Rate 2/min ist der
        # dritte Request 429.
        codes = [self.client.post(url, {}, format="multipart").status_code for _ in range(3)]
        self.assertNotIn(429, codes[:2])
        self.assertEqual(codes[2], 429)

    def test_separate_users_have_separate_budgets(self):
        url = reverse("document-upload")
        for _ in range(3):
            self.client.post(url, {}, format="multipart")
        # Zweiter Nutzer hat ein eigenes Budget → nicht sofort gedrosselt.
        other = get_user_model().objects.create_user(username="up2", password="pw12345!")
        c2 = APIClient()
        c2.force_authenticate(other)
        self.assertNotEqual(c2.post(url, {}, format="multipart").status_code, 429)
