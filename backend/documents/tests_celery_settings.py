"""Regressions-Guard für die Celery-Zuverlässigkeits-Settings (P2).

Diese Settings sind nur SICHER, weil die Verarbeitungs-State-Machine
nebenläufigkeitssicher ist (Compare-and-Swap). Der Test hält die Kombination
fest, damit sie nicht versehentlich entfernt/aufgeweicht wird.
"""
from django.conf import settings
from django.test import SimpleTestCase


class CeleryReliabilitySettingsTests(SimpleTestCase):
    def test_acks_late_und_reject_on_worker_lost(self):
        self.assertTrue(settings.CELERY_TASK_ACKS_LATE)
        self.assertTrue(settings.CELERY_TASK_REJECT_ON_WORKER_LOST)

    def test_prefetch_multiplier_ist_eins(self):
        # Mit acks_late soll kein Worker Tasks vorab horten.
        self.assertEqual(settings.CELERY_WORKER_PREFETCH_MULTIPLIER, 1)

    def test_zeitlimits_hart_groesser_als_soft(self):
        self.assertGreater(settings.CELERY_TASK_SOFT_TIME_LIMIT, 0)
        self.assertGreater(
            settings.CELERY_TASK_TIME_LIMIT, settings.CELERY_TASK_SOFT_TIME_LIMIT
        )
