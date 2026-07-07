"""Tests für das ASN-Feature (Archive Serial Number, STOAA-284/285).

Deckt die Spec-Anforderungen ab:

* ASN – automatische Vergabe, keine Doppelvergabe, Parallelität/Transaktionssicherheit
* QR  – Erzeugung + Inhalt
* OCR – ASN-Erkennung, automatische Versionierung, unbekannte ASN
* API – Dokument über ASN abrufen, QR herunterladen, Suche (ASN12345 == 12345)
"""
import threading

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APITestCase

from .models import ASNCounter, ASNScan, Document, DocumentVersion
from .services import asn as asn_service

User = get_user_model()


def _make_version(document, *, version_no=1, sha256="", ocr_text=""):
    """Hilfsfunktion: legt eine minimale Version an (ohne echte Datei)."""
    version = DocumentVersion.objects.create(
        document=document,
        version_no=version_no,
        file_path=f"/data/originals/doc{document.id}-v{version_no}.pdf",
        sha256=sha256,
        ocr_text=ocr_text,
    )
    document.current_version = version
    document.save(update_fields=["current_version"])
    return version


# ---------------------------------------------------------------------------
# ASN-Vergabe
# ---------------------------------------------------------------------------
class ASNAssignmentTests(TestCase):
    def test_asn_assigned_on_create(self):
        """Jedes neue Dokument erhält automatisch eine ASN (> 0)."""
        doc = Document.objects.create(title="Doc")
        self.assertIsNotNone(doc.asn)
        self.assertGreater(doc.asn, 0)

    def test_asn_sequential_and_gapless(self):
        """Aufeinanderfolgende Dokumente bekommen lückenlose, eindeutige ASNs."""
        docs = [Document.objects.create(title=f"Doc {i}") for i in range(5)]
        asns = sorted(d.asn for d in docs)
        # eindeutig
        self.assertEqual(len(set(asns)), 5)
        # lückenlos fortlaufend (unabhängig vom Startwert)
        self.assertEqual(asns, list(range(asns[0], asns[0] + 5)))

    def test_asn_is_immutable_across_saves(self):
        """Eine einmal vergebene ASN wird durch weitere save() nie verändert."""
        doc = Document.objects.create(title="Doc")
        original = doc.asn
        doc.title = "Doc – umbenannt"
        doc.save()
        doc.refresh_from_db()
        self.assertEqual(doc.asn, original)

    def test_new_version_keeps_document_asn(self):
        """Mehrere Versionen ändern die ASN des Dokuments nicht."""
        doc = Document.objects.create(title="Doc")
        original = doc.asn
        _make_version(doc, version_no=1)
        _make_version(doc, version_no=2)
        doc.refresh_from_db()
        self.assertEqual(doc.asn, original)

    def test_generate_asn_idempotent(self):
        """generate_asn ist idempotent – ein Dokument mit ASN behält sie."""
        doc = Document.objects.create(title="Doc")
        first = asn_service.generate_asn(doc)
        second = asn_service.generate_asn(doc)
        self.assertEqual(first, doc.asn)
        self.assertEqual(first, second)

    def test_allocate_asn_strictly_increasing(self):
        """allocate_asn liefert streng monoton steigende Werte."""
        a = asn_service.allocate_asn()
        b = asn_service.allocate_asn()
        self.assertEqual(b, a + 1)

    def test_no_reuse_after_delete(self):
        """Eine ASN wird nach dem Löschen des Dokuments nicht wiederverwendet."""
        d1 = Document.objects.create(title="A")
        d2 = Document.objects.create(title="B")
        used = {d1.asn, d2.asn}
        d2.delete()
        d3 = Document.objects.create(title="C")
        self.assertNotIn(d3.asn, used)


class ASNConcurrencyTests(TransactionTestCase):
    """Parallelität/Transaktionssicherheit: keine Doppelvergabe unter Last."""

    reset_sequences = True

    def test_parallel_allocation_has_no_duplicates(self):
        from .models import ASNCounter

        # Zähler vor dem Fan-out sicher anlegen (vermeidet Race beim ersten Insert).
        ASNCounter.objects.get_or_create(pk=1, defaults={"last_value": 0})

        threads_count = 5
        per_thread = 10
        results = []
        lock = threading.Lock()

        def worker():
            local = []
            try:
                for _ in range(per_thread):
                    local.append(asn_service.allocate_asn())
            finally:
                # Jede Thread-Verbindung sauber schließen.
                connection.close()
            with lock:
                results.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(threads_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = threads_count * per_thread
        self.assertEqual(len(results), total)
        # Kern-Garantie: keine Doppelvergabe.
        self.assertEqual(len(set(results)), total)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
class ASNParseTests(TestCase):
    def test_parse_asn_strict(self):
        self.assertEqual(asn_service.parse_asn("ASN123"), 123)
        self.assertEqual(asn_service.parse_asn("asn 123"), 123)
        self.assertEqual(asn_service.parse_asn("ASN: 8062"), 8062)
        self.assertEqual(asn_service.parse_asn("A S N\n8062"), 8062)
        self.assertEqual(asn_service.parse_asn("A5N 8062"), 8062)
        self.assertEqual(asn_service.parse_asn("ASN 8O62"), 8062)
        self.assertEqual(asn_service.parse_asn("ASN 8I62"), 8162)
        self.assertEqual(asn_service.parse_asn("Rechnung ASN000045 vom 1.1."), 45)
        self.assertEqual(asn_service.parse_asn("erste ASN12 dann ASN34"), 12)
        self.assertIsNone(asn_service.parse_asn("keine Nummer hier"))
        # Reine Ziffern gelten im OCR-Text NICHT als ASN (nur ASN-präfigiert).
        self.assertIsNone(asn_service.parse_asn("12345"))
        self.assertIsNone(asn_service.parse_asn(""))
        self.assertIsNone(asn_service.parse_asn(None))

    def test_coerce_asn_lenient(self):
        self.assertEqual(asn_service.coerce_asn("ASN123"), 123)
        self.assertEqual(asn_service.coerce_asn("123"), 123)
        self.assertEqual(asn_service.coerce_asn("000123"), 123)
        self.assertEqual(asn_service.coerce_asn("ASN000123"), 123)
        self.assertIsNone(asn_service.coerce_asn("abc"))
        self.assertIsNone(asn_service.coerce_asn(""))
        self.assertIsNone(asn_service.coerce_asn(None))

    def test_format_asn(self):
        self.assertEqual(asn_service.format_asn(123), "ASN000123")
        self.assertEqual(asn_service.format_asn(1234567), "ASN1234567")


# ---------------------------------------------------------------------------
# QR-Code
# ---------------------------------------------------------------------------
class ASNQRTests(TestCase):
    def test_render_qr_returns_png(self):
        doc = Document.objects.create(title="Doc")
        png = asn_service.render_qr(doc)
        self.assertIsInstance(png, bytes)
        # PNG-Signatur.
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_qr_payload_is_only_asn(self):
        doc = Document.objects.create(title="Doc")
        payload = asn_service.qr_payload(doc)
        self.assertEqual(payload, asn_service.format_asn(doc.asn))
        # Enthält weder URL noch JSON.
        self.assertNotIn("http", payload)
        self.assertNotIn("{", payload)
        # Round-trip über den Spec-Regex.
        self.assertEqual(asn_service.parse_asn(payload), doc.asn)


# ---------------------------------------------------------------------------
# OCR-Reconcile (Import-Historie)
# ---------------------------------------------------------------------------
class ASNReconcileTests(TestCase):
    def test_rescan_moves_version_to_existing_document(self):
        """Bekannte ASN im OCR-Text → Version wandert an das bestehende Dokument."""
        existing = Document.objects.create(title="Vertrag 2024")
        _make_version(existing, version_no=1, sha256="a" * 64)

        # Frisch eingescanntes Dokument, dessen OCR-Text die ASN des Bestands trägt.
        rescan = Document.objects.create(title="Scan")
        v_new = _make_version(
            rescan, version_no=1, ocr_text=f"Kopf {asn_service.format_asn(existing.asn)} Fuss"
        )
        rescan_pk = rescan.pk

        result = asn_service.match_and_reconcile(v_new)

        self.assertTrue(result["matched"])
        self.assertTrue(result["moved"])
        v_new.refresh_from_db()
        # Version hängt jetzt am Bestandsdokument, als 2. Version.
        self.assertEqual(v_new.document_id, existing.pk)
        self.assertEqual(v_new.version_no, 2)
        existing.refresh_from_db()
        self.assertEqual(existing.current_version_id, v_new.id)
        self.assertEqual(existing.versions.count(), 2)
        # Kein Duplikat: das versehentlich neue Dokument ist entfernt.
        self.assertFalse(Document.objects.filter(pk=rescan_pk).exists())
        # Import-Historie protokolliert.
        self.assertTrue(
            ASNScan.objects.filter(document=existing, matched_by="OCR").exists()
        )

    def test_unknown_asn_is_claimed_by_current_document(self):
        """Freie erkannte ASN → frisch importiertes Dokument übernimmt das Label."""
        rescan = Document.objects.create(title="Scan")
        detected_asn = rescan.asn + 50
        v_new = _make_version(
            rescan,
            version_no=1,
            ocr_text=f"{asn_service.format_asn(detected_asn)} unbekannt",
        )

        result = asn_service.match_and_reconcile(v_new)

        self.assertTrue(result["matched"])
        self.assertFalse(result["moved"])
        self.assertTrue(result["assigned"])
        v_new.refresh_from_db()
        rescan.refresh_from_db()
        self.assertEqual(v_new.document_id, rescan.pk)
        self.assertEqual(rescan.asn, detected_asn)
        self.assertTrue(
            ASNScan.objects.filter(document=rescan, matched_by="OCR").exists()
        )
        self.assertGreaterEqual(
            ASNCounter.objects.get(pk=1).last_value,
            detected_asn,
        )

    def test_no_asn_in_text_is_noop(self):
        rescan = Document.objects.create(title="Scan")
        v_new = _make_version(rescan, version_no=1, ocr_text="Nur Fliesstext, keine Nummer")

        result = asn_service.match_and_reconcile(v_new)

        self.assertFalse(result["matched"])
        self.assertIsNone(result["asn"])
        self.assertEqual(v_new.document_id, rescan.pk)

    def test_barcode_scanner_uses_documentversion_file_path_and_claims_free_asn(self):
        """QR/Barcode-Scan nutzt file_path und hat Vorrang vor OCR-Text."""
        from unittest import mock

        rescan = Document.objects.create(title="Scan mit QR")
        detected_asn = rescan.asn + 100
        v_new = _make_version(
            rescan,
            version_no=1,
            ocr_text="OCR wuerde ASN000001 liefern",
        )
        v_new.file_path = "/data/originals/scan-mit-qr.pdf"
        v_new.save(update_fields=["file_path"])

        with mock.patch(
            "documents.services.asn_barcode.scan_pdf_for_asn",
            return_value=detected_asn,
        ) as scan_pdf:
            result = asn_service.match_and_reconcile(v_new)

        scan_pdf.assert_called_once_with("/data/originals/scan-mit-qr.pdf")
        self.assertTrue(result["matched"])
        self.assertEqual(result["asn"], detected_asn)
        rescan.refresh_from_db()
        self.assertEqual(rescan.asn, detected_asn)
        self.assertTrue(
            ASNScan.objects.filter(document=rescan, matched_by="BARCODE").exists()
        )

    def test_detect_asn_falls_back_to_existing_ocr_text(self):
        """Backfill/Pipeline erkennen ASN auch ohne Barcode über vorhandenes OCR."""
        rescan = Document.objects.create(title="Scan mit OCR-ASN")
        v_new = _make_version(
            rescan,
            version_no=1,
            ocr_text="Deckblatt\nASN\n8062\nRest",
        )

        asn, matched_by = asn_service.detect_asn(v_new)

        self.assertEqual(asn, 8062)
        self.assertEqual(matched_by, "OCR")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class ASNApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="u", password="pw", role="user")
        cls.other = User.objects.create_user(username="o", password="pw", role="user")
        cls.doc = Document.objects.create(title="Mein Dok", owner=cls.user)
        _make_version(cls.doc, version_no=1, sha256="b" * 64)
        cls.foreign = Document.objects.create(title="Fremd", owner=cls.other)

    def test_by_asn_returns_document(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/by-asn/{self.doc.asn}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], self.doc.id)
        self.assertEqual(resp.data["asn"], self.doc.asn)

    def test_by_asn_accepts_prefixed_form(self):
        self.client.force_authenticate(self.user)
        label = asn_service.format_asn(self.doc.asn)
        resp = self.client.get(f"/api/documents/by-asn/{label}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], self.doc.id)

    def test_by_asn_foreign_document_404(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/by-asn/{self.foreign.asn}/")
        self.assertEqual(resp.status_code, 404)

    def test_qr_endpoint_returns_png(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/{self.doc.id}/qr/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertTrue(resp.content.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_qr_foreign_document_404(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/{self.foreign.id}/qr/")
        self.assertEqual(resp.status_code, 404)

    def test_search_by_asn_number_and_prefixed_are_equivalent(self):
        """Suche: 'ASN12345' und '12345' liefern dasselbe Dokument."""
        self.client.force_authenticate(self.user)
        label = asn_service.format_asn(self.doc.asn)

        resp_num = self.client.get("/api/documents/", {"q": str(self.doc.asn)})
        resp_prefixed = self.client.get("/api/documents/", {"q": label})

        self.assertEqual(resp_num.status_code, 200)
        self.assertEqual(resp_prefixed.status_code, 200)
        ids_num = [d["id"] for d in resp_num.data["results"]]
        ids_prefixed = [d["id"] for d in resp_prefixed.data["results"]]
        self.assertIn(self.doc.id, ids_num)
        self.assertIn(self.doc.id, ids_prefixed)
        self.assertEqual(ids_num, ids_prefixed)

    def test_search_by_asn_scopes_to_owner(self):
        """ASN-Suche respektiert die Owner-Isolation (kein Fremd-Leak)."""
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/documents/", {"q": str(self.foreign.asn)})
        self.assertEqual(resp.status_code, 200)
        ids = [d["id"] for d in resp.data["results"]]
        self.assertNotIn(self.foreign.id, ids)
