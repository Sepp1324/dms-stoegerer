"""Tests für das ASN-Feature (Archive Serial Number) im **Sticker-only-Modell**.

Sticker-only (Nutzer-Entscheidung): Es gibt KEINE automatische ASN-Vergabe mehr.
Ein neues Dokument hat zunächst ``asn = None``; die ASN wird ausschließlich durch
einen erkannten Barcode/QR im OCR-Nachlauf gesetzt (``match_and_reconcile`` →
``_claim_detected_asn``). ``allocate_asn``/``generate_asn`` bleiben als manuelle
Hilfsfunktionen erhalten (z. B. für die Reparatur/Backfill).
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


def _assign(document, asn):
    """Setzt eine ASN direkt (Sticker-only: sonst haben Dokumente keine ASN)."""
    Document.objects.filter(pk=document.pk).update(asn=asn)
    document.asn = asn
    return document


# ---------------------------------------------------------------------------
# ASN-Vergabe (Sticker-only)
# ---------------------------------------------------------------------------
class ASNAssignmentTests(TestCase):
    def test_new_document_has_no_asn(self):
        """Sticker-only: ein neues Dokument bekommt KEINE automatische ASN."""
        doc = Document.objects.create(title="Doc")
        self.assertIsNone(doc.asn)

    def test_assigned_asn_is_immutable_across_saves(self):
        """Eine gesetzte ASN wird durch weitere save() nie verändert."""
        doc = Document.objects.create(title="Doc")
        _assign(doc, 42)
        doc.title = "Doc – umbenannt"
        doc.save()
        doc.refresh_from_db()
        self.assertEqual(doc.asn, 42)

    def test_new_version_keeps_document_asn(self):
        """Mehrere Versionen ändern die ASN des Dokuments nicht."""
        doc = Document.objects.create(title="Doc")
        _assign(doc, 7)
        _make_version(doc, version_no=1)
        _make_version(doc, version_no=2)
        doc.refresh_from_db()
        self.assertEqual(doc.asn, 7)

    def test_generate_asn_idempotent(self):
        """generate_asn (manuell) ist idempotent – ein Dokument mit ASN behält sie."""
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


class ASNConcurrencyTests(TransactionTestCase):
    """Parallelität/Transaktionssicherheit: keine Doppelvergabe unter Last."""

    reset_sequences = True

    def test_parallel_allocation_has_no_duplicates(self):
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
        self.assertEqual(len(set(results)), total)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
class ASNParseTests(TestCase):
    def test_parse_asn_strict(self):
        self.assertEqual(asn_service.parse_asn("ASN123"), 123)
        self.assertEqual(asn_service.parse_asn("asn 123"), 123)
        self.assertEqual(asn_service.parse_asn("ASN: 8062"), 8062)
        self.assertEqual(asn_service.parse_asn("Rechnung ASN000045 vom 1.1."), 45)
        self.assertIsNone(asn_service.parse_asn("keine Nummer hier"))
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
        _assign(doc, 123)
        png = asn_service.render_qr(doc)
        self.assertIsInstance(png, bytes)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_qr_payload_is_only_asn(self):
        doc = Document.objects.create(title="Doc")
        _assign(doc, 123)
        payload = asn_service.qr_payload(doc)
        self.assertEqual(payload, asn_service.format_asn(123))
        self.assertNotIn("http", payload)
        self.assertNotIn("{", payload)

    def test_qr_payload_without_asn_raises(self):
        """Sticker-only: ohne ASN gibt es keinen QR."""
        doc = Document.objects.create(title="Ohne Sticker")
        with self.assertRaises(asn_service.NoASNError):
            asn_service.qr_payload(doc)


# ---------------------------------------------------------------------------
# OCR-/Barcode-Reconcile
# ---------------------------------------------------------------------------
class ASNReconcileTests(TestCase):
    def test_rescan_moves_version_to_existing_document(self):
        """Bekannte ASN im OCR-Text → Version wandert an das bestehende Dokument."""
        existing = Document.objects.create(title="Vertrag 2024")
        _assign(existing, 55)
        _make_version(existing, version_no=1, sha256="a" * 64)

        rescan = Document.objects.create(title="Scan")
        v_new = _make_version(
            rescan, version_no=1, ocr_text=f"Kopf {asn_service.format_asn(55)} Fuss"
        )
        rescan_pk = rescan.pk

        result = asn_service.match_and_reconcile(v_new)

        self.assertTrue(result["matched"])
        self.assertTrue(result["moved"])
        v_new.refresh_from_db()
        self.assertEqual(v_new.document_id, existing.pk)
        self.assertEqual(v_new.version_no, 2)
        existing.refresh_from_db()
        self.assertEqual(existing.current_version_id, v_new.id)
        self.assertEqual(existing.versions.count(), 2)
        self.assertFalse(Document.objects.filter(pk=rescan_pk).exists())
        self.assertTrue(
            ASNScan.objects.filter(document=existing, matched_by="OCR").exists()
        )

    def test_unknown_ocr_asn_is_not_claimed(self):
        """OCR-Text darf KEINE neue ASN beanspruchen (nur Barcode/QR)."""
        rescan = Document.objects.create(title="Scan")
        v_new = _make_version(
            rescan, version_no=1, ocr_text=f"{asn_service.format_asn(9042)} unbekannt"
        )
        counter_before = ASNCounter.objects.get_or_create(
            pk=1, defaults={"last_value": 0}
        )[0].last_value

        result = asn_service.match_and_reconcile(v_new)

        self.assertFalse(result["matched"])
        self.assertEqual(result.get("reason"), "text_only")
        rescan.refresh_from_db()
        self.assertIsNone(rescan.asn)  # Sticker-only: keine ASN ohne Barcode
        self.assertEqual(ASNCounter.objects.get(pk=1).last_value, counter_before)
        self.assertFalse(ASNScan.objects.filter(document=rescan).exists())

    def test_no_asn_in_text_is_noop(self):
        rescan = Document.objects.create(title="Scan")
        v_new = _make_version(rescan, version_no=1, ocr_text="Nur Fliesstext, keine Nummer")

        result = asn_service.match_and_reconcile(v_new)

        self.assertFalse(result["matched"])
        self.assertIsNone(result["asn"])
        self.assertEqual(v_new.document_id, rescan.pk)
        rescan.refresh_from_db()
        self.assertIsNone(rescan.asn)

    def test_barcode_claims_free_asn_onto_document(self):
        """QR/Barcode-Scan hat Vorrang und setzt die aufgeklebte ASN aufs Dokument."""
        from unittest import mock

        rescan = Document.objects.create(title="Scan mit QR")
        v_new = _make_version(
            rescan, version_no=1, ocr_text="OCR wuerde ASN000001 liefern"
        )
        v_new.file_path = "/data/originals/scan-mit-qr.pdf"
        v_new.save(update_fields=["file_path"])

        with mock.patch(
            "documents.services.asn_barcode.scan_pdf_for_asn",
            return_value=777,
        ) as scan_pdf:
            result = asn_service.match_and_reconcile(v_new)

        scan_pdf.assert_called_once_with("/data/originals/scan-mit-qr.pdf")
        self.assertTrue(result["matched"])
        self.assertEqual(result["asn"], 777)
        rescan.refresh_from_db()
        self.assertEqual(rescan.asn, 777)
        self.assertTrue(
            ASNScan.objects.filter(document=rescan, matched_by="BARCODE").exists()
        )

    def test_detect_asn_falls_back_to_existing_ocr_text(self):
        rescan = Document.objects.create(title="Scan mit OCR-ASN")
        v_new = _make_version(
            rescan, version_no=1, ocr_text="Deckblatt\nASN\n8062\nRest"
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
        _assign(cls.doc, 4242)
        cls.foreign = Document.objects.create(title="Fremd", owner=cls.other)
        _assign(cls.foreign, 5151)

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

    def test_qr_without_asn_returns_404(self):
        """Sticker-only: Dokument ohne ASN hat keinen QR."""
        self.client.force_authenticate(self.user)
        doc = Document.objects.create(title="Ohne Sticker", owner=self.user)
        resp = self.client.get(f"/api/documents/{doc.id}/qr/")
        self.assertEqual(resp.status_code, 404)

    def test_qr_foreign_document_404(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/{self.foreign.id}/qr/")
        self.assertEqual(resp.status_code, 404)

    def test_search_by_asn_number_and_prefixed_are_equivalent(self):
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
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/documents/", {"q": str(self.foreign.asn)})
        self.assertEqual(resp.status_code, 200)
        ids = [d["id"] for d in resp.data["results"]]
        self.assertNotIn(self.foreign.id, ids)


# ---------------------------------------------------------------------------
# Reparatur / Alt-ASNs leeren
# ---------------------------------------------------------------------------
class RepairAsnCommandTests(TestCase):
    def test_repair_renumbers_poisoned_and_resets_counter(self):
        from django.core.management import call_command

        clean = Document.objects.create(title="Sauber")
        _assign(clean, 5)
        poisoned = Document.objects.create(title="Vergiftet")
        _assign(poisoned, 99999)
        ASNCounter.objects.update_or_create(pk=1, defaults={"last_value": 99999})

        call_command("repair_asn", threshold=1000)

        poisoned.refresh_from_db()
        self.assertLess(poisoned.asn, 1000)
        self.assertGreater(poisoned.asn, clean.asn)
        self.assertEqual(ASNCounter.objects.get(pk=1).last_value, poisoned.asn)

    def test_repair_dry_run_changes_nothing(self):
        from django.core.management import call_command

        poisoned = Document.objects.create(title="Vergiftet")
        _assign(poisoned, 99999)

        call_command("repair_asn", threshold=1000, dry_run=True)

        poisoned.refresh_from_db()
        self.assertEqual(poisoned.asn, 99999)


class ClearAutoAsnCommandTests(TestCase):
    def test_clear_nulls_all_asns(self):
        """clear_auto_asn leert alle vorhandenen ASNs (für Neu-Bekleben)."""
        from django.core.management import call_command

        doc = Document.objects.create(title="Alt")
        _assign(doc, 12)

        call_command("clear_auto_asn", yes=True)

        doc.refresh_from_db()
        self.assertIsNone(doc.asn)
        self.assertEqual(ASNCounter.objects.get(pk=1).last_value, 0)
