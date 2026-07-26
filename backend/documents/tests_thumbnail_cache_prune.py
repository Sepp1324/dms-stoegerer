"""P2: Der persistente Werkbank-Thumbnail-Cache wird per TTL + Größe begrenzt."""
import os
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from . import tasks


class ThumbnailCachePruneTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _write(self, name: str, *, size: int, age_days: float):
        fp = self.root / name
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_bytes(b"x" * size)
        old = time.time() - age_days * 86400
        os.utime(fp, (old, old))
        return fp

    def test_ttl_entfernt_alte_dateien(self):
        alt = self._write("10/1_110.jpg", size=10, age_days=30)
        frisch = self._write("10/2_110.jpg", size=10, age_days=1)
        with override_settings(
            WORKBENCH_THUMB_CACHE_DIR=str(self.root),
            WORKBENCH_THUMB_CACHE_TTL_DAYS=14,
            WORKBENCH_THUMB_CACHE_MAX_MB=1024,
        ):
            res = tasks.prune_workbench_thumbnail_cache()
        self.assertFalse(alt.exists())     # > TTL -> entfernt
        self.assertTrue(frisch.exists())   # innerhalb TTL -> bleibt
        self.assertGreaterEqual(res["removed"], 1)

    def test_groessenlimit_entfernt_aelteste_zuerst(self):
        # 3 frische Dateien je 1 MiB, Limit 2 MiB -> die aelteste faellt raus.
        mb = 1024 * 1024
        a = self._write("v/a.jpg", size=mb, age_days=3)  # aeltest
        b = self._write("v/b.jpg", size=mb, age_days=2)
        c = self._write("v/c.jpg", size=mb, age_days=1)  # neueste
        with override_settings(
            WORKBENCH_THUMB_CACHE_DIR=str(self.root),
            WORKBENCH_THUMB_CACHE_TTL_DAYS=365,
            WORKBENCH_THUMB_CACHE_MAX_MB=2,
        ):
            tasks.prune_workbench_thumbnail_cache()
        self.assertFalse(a.exists())  # aelteste -> LRU-Opfer
        self.assertTrue(b.exists())
        self.assertTrue(c.exists())

    def test_loeschfehler_wird_als_fehler_gemeldet(self):
        # P2: Schlaegt eine Loeschung fehl, darf der Task NICHT still Erfolg melden
        # (sonst bliebe der Speicherverlust im Monitoring unsichtbar) -> Task-Fehler.
        self._write("v/old.jpg", size=10, age_days=30)
        with override_settings(
            WORKBENCH_THUMB_CACHE_DIR=str(self.root),
            WORKBENCH_THUMB_CACHE_TTL_DAYS=14,
            WORKBENCH_THUMB_CACHE_MAX_MB=1024,
        ), mock.patch.object(tasks.os, "remove", side_effect=OSError("EACCES")):
            with self.assertRaises(OSError):
                tasks.prune_workbench_thumbnail_cache()
