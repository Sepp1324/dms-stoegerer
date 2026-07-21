"""Regressions-Guard für die Celery-Zuverlässigkeits-Settings.

acks_late/reject_on_worker_lost sind BEWUSST NICHT global aktiviert: ohne einen
wiederaufnahmefähigen process_document_version (atomarer Claim) und ohne
pro-Task-Scoping auf idempotente Tasks würde Redelivery Dokumente fälschlich
FAILED setzen bzw. nicht-idempotente Tasks (psychosr/KI/E-Mail) doppelt
ausführen. Zeitlimits + prefetch bleiben (sicher). Der Test hält diesen Stand
fest, damit acks_late nicht versehentlich wieder global aktiviert wird.
"""
from django.conf import settings
from django.test import SimpleTestCase


class CeleryReliabilitySettingsTests(SimpleTestCase):
    def test_acks_late_nicht_global_aktiviert(self):
        # Nicht global setzen (Default False). Erst mit wiederaufnahmefähigem
        # Task + pro-Task-Scoping sicher aktivierbar.
        self.assertFalse(getattr(settings, "CELERY_TASK_ACKS_LATE", False))
        self.assertFalse(getattr(settings, "CELERY_TASK_REJECT_ON_WORKER_LOST", False))

    def test_prefetch_multiplier_ist_eins(self):
        self.assertEqual(settings.CELERY_WORKER_PREFETCH_MULTIPLIER, 1)

    def test_zeitlimits_hart_groesser_als_soft(self):
        self.assertGreater(settings.CELERY_TASK_SOFT_TIME_LIMIT, 0)
        self.assertGreater(
            settings.CELERY_TASK_TIME_LIMIT, settings.CELERY_TASK_SOFT_TIME_LIMIT
        )
