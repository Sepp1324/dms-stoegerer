"""Sicherheits-Tests: Consume-Scanner darf keine Symlinks dereferenzieren.

Angriff: Ein schreibberechtigter NFS-Nutzer legt einen Symlink (z. B. auf
/proc/self/environ) in den Consume-Ordner und importiert so Worker-Secrets als
eigenes Dokument. Der Scanner muss Symlinks/Nicht-Regulärdateien verwerfen und
reguläre Dateien symlink-sicher (O_NOFOLLOW) mit Größenlimit lesen.
"""
import os
import tempfile
from pathlib import Path

from django.test import TestCase

from documents import tasks


class ReadRegularNoFollowTests(TestCase):
    def test_reads_regular_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.txt"
            p.write_bytes(b"hello")
            self.assertEqual(tasks._read_regular_nofollow(p, 1000), b"hello")

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            secret = Path(d) / "secret"
            secret.write_bytes(b"TOPSECRET")
            link = Path(d) / "evil.pdf"
            link.symlink_to(secret)
            with self.assertRaises(OSError):
                tasks._read_regular_nofollow(link, 1000)

    def test_rejects_oversize(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "big.bin"
            p.write_bytes(b"x" * 100)
            with self.assertRaises(OSError):
                tasks._read_regular_nofollow(p, 10)


class IngestSymlinkTests(TestCase):
    def test_symlink_is_dropped_not_ingested(self):
        # Das Angriffsziel liegt BEWUSST außerhalb des gescannten Consume-Ordners
        # (so wie /proc/self/environ o. Ä.). Läge es im Ordner selbst, würde der
        # Scanner es als eigene reguläre Datei aufnehmen – das ist nicht der zu
        # prüfende Pfad. Geprüft wird: der Symlink im Eingang wird verworfen
        # (nur der Link entfernt), und das Ziel bleibt unberührt.
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as d:
            base = Path(d)
            secret = Path(outside) / "outside_secret"
            secret.write_bytes(b"SECRET-ENV")
            link = base / "evil.pdf"
            link.symlink_to(secret)

            # min_age=0 → Reife kein Faktor; now weit in der Zukunft.
            result = tasks._ingest_consume_dir(
                base, owner=None, min_age=0, now=link.lstat().st_mtime + 10_000
            )

            # Nichts importiert …
            self.assertEqual(result["ingested"], [])
            # … der Link ist weg (nur der Link, nicht das Ziel) …
            self.assertFalse(os.path.lexists(link))
            # … und das Ziel-Secret ist unberührt.
            self.assertTrue(secret.exists())
            self.assertEqual(secret.read_bytes(), b"SECRET-ENV")
