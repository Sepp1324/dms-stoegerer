"""Tests für die OCR-Qualitätsheuristik ``is_valid_ocr``.

Kern von P2(a): die Bewertung hängt an ``len(text) / pages``. Eine zu KLEINE
Seitenzahl täuscht „genug Text pro Seite" vor. ``run_ocr`` liest die Seitenzahl
deshalb aus dem PDF (nicht mehr aus Zeilenumbrüchen) – dieser Test hält fest,
dass die Seitenzahl das Ergebnis tatsächlich kippt.
"""
from django.test import SimpleTestCase

from documents.services.ocr.validate import is_valid_ocr


class IsValidOcrTests(SimpleTestCase):
    def test_leerer_text_ist_ungueltig(self):
        self.assertFalse(is_valid_ocr("", 1))

    def test_genug_zeichen_pro_seite_ist_gueltig(self):
        self.assertTrue(is_valid_ocr("x" * 100, 1))

    def test_zu_wenig_zeichen_pro_seite_ist_ungueltig(self):
        # < 20 Zeichen/Seite -> ungültig.
        self.assertFalse(is_valid_ocr("x" * 10, 1))

    def test_seitenzahl_kippt_das_ergebnis(self):
        # Derselbe Text: bei 1 Seite gültig (60/Seite), bei 4 Seiten ungültig
        # (15/Seite). Genau deshalb muss die ECHTE Seitenzahl verwendet werden –
        # eine unterschätzte Seitenzahl ließe lückenhafte OCR als SUCCESS gelten.
        text = "x" * 60
        self.assertTrue(is_valid_ocr(text, 1))
        self.assertFalse(is_valid_ocr(text, 4))
