"""P2: Bei Cache-Ausfall fällt das Throttle auf einen prozess-lokalen Zähler
zurück – statt vollständig offen (fail-open) zu sein."""
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from rest_framework.throttling import SimpleRateThrottle

from documents import throttling


class _FallbackThrottle(throttling._PerUserScopeThrottle):
    scope = "login"  # existierende Rate; im Test überschrieben


class ThrottleLocalFallbackTests(TestCase):
    def setUp(self):
        throttling._FALLBACK_HISTORY.clear()
        self.factory = RequestFactory()

    def _throttle(self, num_requests=2, duration=60):
        t = _FallbackThrottle()
        # Rate deterministisch setzen (unabhängig von den Settings-Raten).
        t.rate = f"{num_requests}/min"
        t.num_requests = num_requests
        t.duration = duration
        return t

    def _request(self, ip="203.0.113.7"):
        req = self.factory.post("/api/auth/token/")
        req.META["REMOTE_ADDR"] = ip
        req.user = None  # unauthentifiziert -> Key über Client-IP
        return req

    def test_fallback_drosselt_bei_cache_ausfall(self):
        t = self._throttle(num_requests=2)
        req = self._request()
        # super().allow_request (Redis) wirft -> Fallback greift.
        with patch.object(
            SimpleRateThrottle, "allow_request", side_effect=ConnectionError("redis down")
        ):
            results = [t.allow_request(req, view=None) for _ in range(4)]
        # Erste 2 erlaubt, danach gesperrt (statt alle 4 offen).
        self.assertEqual(results, [True, True, False, False])

    def test_gedrosselter_fallback_liefert_wait_ohne_500(self):
        # P1-Regression: Nach allow_request()->False ruft DRF wait() für den
        # Retry-After-Header auf und greift auf self.history/self.now zu. Ohne die
        # gesetzten DRF-Attribute crashte das mit AttributeError -> HTTP 500.
        t = self._throttle(num_requests=1, duration=60)
        req = self._request()
        with patch.object(
            SimpleRateThrottle, "allow_request", side_effect=ConnectionError("redis down")
        ):
            self.assertTrue(t.allow_request(req, view=None))  # 1. erlaubt
            self.assertFalse(t.allow_request(req, view=None))  # 2. gedrosselt
            # DRF-Pfad: darf NICHT mit AttributeError brechen.
            wait = t.wait()
        self.assertIsInstance(wait, float)
        self.assertGreaterEqual(wait, 0.0)
        self.assertLessEqual(wait, 60.0)

    def test_fallback_ist_pro_key_unabhaengig(self):
        t = self._throttle(num_requests=1)
        a, b = self._request("198.51.100.1"), self._request("198.51.100.2")
        with patch.object(
            SimpleRateThrottle, "allow_request", side_effect=ConnectionError("redis down")
        ):
            first_a = t.allow_request(a, view=None)
            first_b = t.allow_request(b, view=None)
            second_a = t.allow_request(a, view=None)
        self.assertTrue(first_a)
        self.assertTrue(first_b)  # andere IP -> eigener Zähler
        self.assertFalse(second_a)  # dieselbe IP -> Limit erreicht

    def test_ohne_rate_wird_nicht_gedrosselt(self):
        t = self._throttle()
        t.rate = None
        req = self._request()
        with patch.object(
            SimpleRateThrottle, "allow_request", side_effect=ConnectionError("redis down")
        ):
            results = [t.allow_request(req, view=None) for _ in range(5)]
        self.assertEqual(results, [True] * 5)

    def test_normalpfad_unberuehrt(self):
        # Ohne Cache-Fehler bleibt das Verhalten der Basisklasse maßgeblich.
        t = self._throttle()
        req = self._request()
        with patch.object(SimpleRateThrottle, "allow_request", return_value=True) as sup:
            self.assertTrue(t.allow_request(req, view=None))
        sup.assert_called_once()
        # Der Fallback-Store bleibt im Normalpfad leer.
        self.assertEqual(throttling._FALLBACK_HISTORY, {})
