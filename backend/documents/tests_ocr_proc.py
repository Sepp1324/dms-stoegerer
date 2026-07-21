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
