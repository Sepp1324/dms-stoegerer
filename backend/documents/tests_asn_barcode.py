"""Tests für die ASN-Barcode-/QR-Erkennung (asn_barcode)."""
from django.test import TestCase

from documents.services import asn_barcode as ab


class AsnBarcodeExtractTests(TestCase):
    def test_extract_from_prefixed_payload(self):
        self.assertEqual(ab._extract_asn_from_payload("ASN00011", "ASN"), 11)
        self.assertEqual(ab._extract_asn_from_payload("asn 42", "ASN"), 42)
        self.assertIsNone(ab._extract_asn_from_payload("Rechnung 2026", "ASN"))
        self.assertIsNone(ab._extract_asn_from_payload("00011", "ASN"))  # ohne Präfix

    def test_decode_variants_includes_binarized(self):
        """Es werden mehrere Varianten (inkl. Threshold) fürs Decoding erzeugt."""
        try:
            from PIL import Image
        except Exception:  # pragma: no cover
            self.skipTest("Pillow nicht verfügbar")
        img = Image.new("RGB", (40, 40), "white")
        variants = ab._decode_variants(img)
        # Rohbild + Graustufe + Autokontrast + Threshold
        self.assertGreaterEqual(len(variants), 3)


class AsnBarcodeDecodeTests(TestCase):
    def test_generated_qr_is_detected(self):
        """Ein QR mit ``ASN00011`` wird über die Decode-Varianten als ASN 11 erkannt."""
        try:
            import qrcode
            from pyzbar.pyzbar import decode
        except Exception:  # pragma: no cover - libzbar/pyzbar nur im Backend-Image
            self.skipTest("qrcode/pyzbar/libzbar nicht verfügbar")

        img = qrcode.make("ASN00011").convert("RGB")

        found = None
        for variant in ab._decode_variants(img):
            for barcode in decode(variant):
                asn = ab._extract_asn_from_payload(
                    barcode.data.decode("utf-8", "ignore"), "ASN"
                )
                if asn is not None:
                    found = asn
                    break
            if found is not None:
                break

        self.assertEqual(found, 11)
