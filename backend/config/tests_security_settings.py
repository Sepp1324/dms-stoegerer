"""Tests der HTTPS-/Sicherheits-Härtung (P2)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BACKEND_DIR = Path(__file__).resolve().parent.parent  # .../backend


class AlwaysOnSecurityTests(SimpleTestCase):
    def test_cookie_and_nosniff_defaults(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)


class SecretKeyFailClosedTests(SimpleTestCase):
    def _setup_in_subprocess(self, env_overrides: dict) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["DJANGO_SETTINGS_MODULE"] = "config.settings"
        env.update(env_overrides)
        return subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(BACKEND_DIR),
        )

    def test_production_without_secret_key_refuses_to_start(self):
        # DEBUG=false + unsicherer Default-SECRET_KEY -> Settings-Import scheitert.
        proc = self._setup_in_subprocess(
            {"DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": "insecure-dev-key-change-me"}
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", proc.stderr)

    def test_production_with_secret_key_starts(self):
        proc = self._setup_in_subprocess(
            {"DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": "a-real-strong-secret-value"}
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
