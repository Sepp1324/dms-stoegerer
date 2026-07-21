"""Tests für den OCR-Subprozess-Helper und den SoftTimeLimit-Durchlass.

run_group setzt ein hartes Timeout + Prozessgruppen-Kill; die OCR-Engine tarnt
ein SoftTimeLimitExceeded nicht mehr als FAILED, sondern lässt es durch (Task
bricht ab, statt die Version trotz Timeout READY erreichen zu lassen).
"""
import subprocess
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.test import SimpleTestCase

from documents.services.ocr import engine
from documents.services.ocr._proc import run_group
from documents.services.ocr.types import OCRStatusEnum


class RunGroupTests(SimpleTestCase):
    def test_erfolg_liefert_stdout(self):
        out = run_group(
            ["python3", "-c", "import sys; sys.stdout.write('hallo')"],
            timeout=10,
            capture=True,
        )
        self.assertEqual(out, b"hallo")

    def test_timeout_wirft_timeoutexpired(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_group(["sleep", "5"], timeout=1)

    def test_nonzero_exit_wirft_calledprocesserror(self):
        with self.assertRaises(subprocess.CalledProcessError):
            run_group(["false"])

    def test_abbruch_killt_die_prozessgruppe(self):
        # Direkter Test auf run_group: bei Timeout (gleicher Kill-Pfad wie
        # SoftTimeLimit) wird der Kindprozess GEKILLT – er schreibt seine
        # Marker-Datei nach 2 s daher NICHT mehr.
        import os
        import tempfile
        import time

        marker = os.path.join(tempfile.mkdtemp(), "written")
        cmd = [
            "python3",
            "-c",
            f"import time; time.sleep(2); open({marker!r}, 'w').close()",
        ]
        with self.assertRaises(subprocess.TimeoutExpired):
            run_group(cmd, timeout=1)
        time.sleep(2.5)
        self.assertFalse(os.path.exists(marker), "Kindprozess wurde nicht gekillt")


def _write_pdf(path: str) -> None:
    import fitz

    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()


class RunOcrArchiveTests(SimpleTestCase):
    def setUp(self):
        import os
        import tempfile

        self.dir = tempfile.mkdtemp()
        self.input = os.path.join(self.dir, "src.pdf")
        _write_pdf(self.input)
        from pathlib import Path

        self.final = str(Path(self.input).with_suffix(".ocr.pdf"))

    def test_timeout_wird_als_verarbeitungsfehler_geworfen(self):
        # OCR-Prozess-Timeout ist ein harter, retryfähiger Fehler -> weiterwerfen.
        with mock.patch.object(
            engine, "extract_text_best_effort", return_value="x" * 100
        ), mock.patch.object(
            engine, "run_group", side_effect=subprocess.TimeoutExpired("ocrmypdf", 1)
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                engine.run_ocr(self.input)

    def test_erfolg_schreibt_archiv_atomar(self):
        import os

        def _fake_ocrmypdf(cmd, **kw):
            _write_pdf(cmd[-1])  # ocrmypdf schreibt gültiges PDF an die tmp-Ausgabe
            return b""

        with mock.patch.object(
            engine, "extract_text_best_effort", return_value="x" * 100
        ), mock.patch.object(engine, "run_group", side_effect=_fake_ocrmypdf):
            result = engine.run_ocr(self.input)

        self.assertEqual(result.status, OCRStatusEnum.SUCCESS)
        self.assertTrue(os.path.exists(self.final))
        self.assertFalse(
            [f for f in os.listdir(self.dir) if f.endswith(".tmp.pdf")],
            "Temp-Datei nicht aufgeräumt",
        )

    def test_fehler_hinterlaesst_kein_archiv_und_keine_tempdatei(self):
        import os

        with mock.patch.object(
            engine, "extract_text_best_effort", return_value="x" * 100
        ), mock.patch.object(engine, "run_group", side_effect=RuntimeError("boom")):
            result = engine.run_ocr(self.input)

        self.assertEqual(result.status, OCRStatusEnum.FAILED)
        self.assertFalse(os.path.exists(self.final), "partielles/altes Archiv übernommen")
        self.assertFalse([f for f in os.listdir(self.dir) if f.endswith(".tmp.pdf")])


class RunOcrSoftLimitTests(SimpleTestCase):
    def test_run_ocr_reicht_soft_limit_durch(self):
        # SoftTimeLimit während ocrmypdf darf NICHT als OCRStatus.FAILED
        # zurückkommen (sonst liefe die Pipeline bis READY weiter).
        with mock.patch.object(
            engine, "extract_text_best_effort", return_value=""
        ), mock.patch.object(
            engine, "_pdf_page_count", return_value=1
        ), mock.patch.object(
            engine, "run_group", side_effect=SoftTimeLimitExceeded()
        ):
            with self.assertRaises(SoftTimeLimitExceeded):
                engine.run_ocr("/tmp/whatever.pdf")
