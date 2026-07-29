"""Tests der HTTPS-/Sicherheits-Härtung (P2)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase

from config.settings import _with_redis_auth

BACKEND_DIR = Path(__file__).resolve().parent.parent  # .../backend


class RedisAuthUrlTests(SimpleTestCase):
    def test_weaves_password_into_url(self):
        with mock.patch.dict(os.environ, {"REDIS_PASSWORD": "s3cr3t"}):
            self.assertEqual(
                _with_redis_auth("redis://redis:6379/0"),
                "redis://:s3cr3t@redis:6379/0",
            )

    def test_special_chars_are_url_encoded(self):
        # base64-typische Sonderzeichen (+/=) und ein : dürfen die URL nicht
        # zerlegen – sie müssen prozentkodiert werden.
        with mock.patch.dict(os.environ, {"REDIS_PASSWORD": "a+b/c=d:1a"}):
            self.assertEqual(
                _with_redis_auth("redis://redis:6379/0"),
                "redis://:a%2Bb%2Fc%3Dd%3A1a@redis:6379/0",
            )

    def test_url_with_existing_auth_unchanged(self):
        with mock.patch.dict(os.environ, {"REDIS_PASSWORD": "s3cr3t"}):
            self.assertEqual(
                _with_redis_auth("redis://:pre@redis:6379/0"),
                "redis://:pre@redis:6379/0",
            )

    def test_no_password_leaves_url_unchanged(self):
        env = dict(os.environ)
        env.pop("REDIS_PASSWORD", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                _with_redis_auth("redis://redis:6379/0"),
                "redis://redis:6379/0",
            )


class AlwaysOnSecurityTests(SimpleTestCase):
    def test_cookie_and_nosniff_defaults(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)


class CacheRedisAuthTests(SimpleTestCase):
    """P1: Der Cache MUSS – wie der Broker – REDIS_PASSWORD in CACHE_URL einweben.

    Sonst fallen alle Cache-Nutzer (Throttling, Qualitäts-Status) in Prod mit
    NOAUTH → HTTP 500 aus. Wir importieren die Settings in einem Subprozess mit
    gesetzten Env-Vars und lesen die effektive CACHES-LOCATION aus.
    """

    def _cache_location(self, env_overrides: dict) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["DJANGO_SETTINGS_MODULE"] = "config.settings"
        env.update(env_overrides)
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import django; django.setup();"
                "from django.conf import settings;"
                "print(settings.CACHES['default'].get('LOCATION',''))",
            ],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(BACKEND_DIR),
        )

    def test_cache_url_gets_password_woven_in(self):
        proc = self._cache_location(
            {
                "DJANGO_DEBUG": "false",
                "DJANGO_SECRET_KEY": "a-real-strong-secret-value",
                "CACHE_URL": "redis://redis:6379/1",
                "REDIS_PASSWORD": "s3cr3t",
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("redis://:s3cr3t@redis:6379/1", proc.stdout)

    def test_cache_url_without_password_unchanged(self):
        proc = self._cache_location(
            {
                "DJANGO_DEBUG": "false",
                "DJANGO_SECRET_KEY": "a-real-strong-secret-value",
                "CACHE_URL": "redis://redis:6379/1",
                "REDIS_PASSWORD": "",
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("redis://redis:6379/1", proc.stdout)
        self.assertNotIn("@", proc.stdout)


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
