"""P2: Der Consume-Leser schneidet eine noch wachsende/zu große Datei NICHT mehr
still ab, sondern bricht ab (Abbruch beim Zusatz-Byte + Stabilitätsprüfung)."""
import os
import stat
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from .tasks import _read_regular_nofollow


class _FakeStat:
    def __init__(self, size, mtime_ns, mode=0o100644):
        self.st_mode = mode
        self.st_size = size
        self.st_mtime_ns = mtime_ns


class ConsumeReadTests(SimpleTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _write(self, data: bytes) -> Path:
        p = Path(self.dir) / "scan.pdf"
        p.write_bytes(data)
        return p

    def test_liest_datei_am_limit_vollstaendig(self):
        content = b"A" * 100
        path = self._write(content)
        self.assertEqual(_read_regular_nofollow(path, max_bytes=100), content)

    def test_zu_grosse_datei_wirft(self):
        path = self._write(b"B" * 101)
        with self.assertRaises(OSError):
            _read_regular_nofollow(path, max_bytes=100)

    def test_wachsende_datei_wird_nicht_abgeschnitten(self):
        # fstat meldet zunächst 50 Bytes (<= Limit), die Datei enthält aber 120 –
        # der Read liefert mehr als max_bytes -> Abbruch statt stiller Kürzung.
        path = self._write(b"C" * 120)
        fake = _FakeStat(size=50, mtime_ns=1000)
        with mock.patch("documents.tasks.os.fstat", return_value=fake):
            with self.assertRaises(OSError):
                _read_regular_nofollow(path, max_bytes=100)

    def test_aenderung_waehrend_lesens_wirft(self):
        path = self._write(b"D" * 40)
        before = _FakeStat(size=40, mtime_ns=1000)
        after = _FakeStat(size=40, mtime_ns=2000)  # mtime geändert -> instabil
        with mock.patch("documents.tasks.os.fstat", side_effect=[before, after]):
            with self.assertRaises(OSError):
                _read_regular_nofollow(path, max_bytes=100)
