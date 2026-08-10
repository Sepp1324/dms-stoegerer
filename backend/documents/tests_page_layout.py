"""Tests für die wortgenaue Seiten-Geometrie (``page_layout``) – Studio Phase 1.

``extract_page_layout`` liest PDFs seitenweise (PyMuPDF) und liefert je Seite die
Wortkästen (``bbox``) samt Seitenmaßen. Für Nicht-PDFs, fehlende/defekte oder
textlose Dateien bleibt das Ergebnis leer (nie ein Abbruch). ``write_page_layout``
ersetzt das Layout einer Version idempotent. Zusätzlich: der ``page-layout``-Endpoint
(Owner-Isolation) und die Verankerungsfelder auf ``ExtractionCandidate``.
"""
import os
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import pipeline
from .models import (
    Document,
    DocumentPageLayout,
    DocumentVersion,
    ExtractionCandidate,
)
from .services import page_layout

User = get_user_model()


def _text_pdf(path: Path, pages_text: list[str]) -> None:
    import fitz

    doc = fitz.open()
    for body in pages_text:
        page = doc.new_page()
        if body:
            page.insert_text((72, 72), body)
    doc.save(str(path))
    doc.close()


class ExtractPageLayoutTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _path(self, name: str) -> Path:
        return Path(self.tmp.name) / name

    def test_pdf_liefert_woerter_mit_bbox_und_seitenmassen(self):
        path = self._path("worte.pdf")
        _text_pdf(path, ["Rechnung Betrag"])

        pages = page_layout.extract_page_layout(path)

        self.assertEqual(len(pages), 1)
        first = pages[0]
        self.assertEqual(first["page_no"], 1)
        self.assertGreater(first["width"], 0)
        self.assertGreater(first["height"], 0)
        texte = [w["t"] for w in first["words"]]
        self.assertIn("Rechnung", texte)
        self.assertIn("Betrag", texte)
        # bbox = [x0, y0, x1, y1] mit x0<x1, y0<y1 und plausibel nahe (72, 72).
        box = first["words"][0]["bbox"]
        self.assertEqual(len(box), 4)
        self.assertLess(box[0], box[2])
        self.assertLess(box[1], box[3])
        self.assertLess(box[0], 120)

    def test_mehrere_seiten_behalten_ihre_nummer(self):
        path = self._path("zwei.pdf")
        _text_pdf(path, ["Seite eins", "Seite zwei"])

        pages = page_layout.extract_page_layout(path)

        self.assertEqual([p["page_no"] for p in pages], [1, 2])

    def test_textlose_seiten_werden_uebersprungen(self):
        path = self._path("gemischt.pdf")
        _text_pdf(path, ["", "Nur hier Text"])

        pages = page_layout.extract_page_layout(path)

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["page_no"], 2)

    def test_nicht_pdf_liefert_leer(self):
        path = self._path("egal.txt")
        path.write_text("kein pdf")
        self.assertEqual(page_layout.extract_page_layout(path), [])

    def test_fehlende_datei_liefert_leer(self):
        self.assertEqual(page_layout.extract_page_layout(self._path("weg.pdf")), [])

    def test_defektes_pdf_liefert_leer(self):
        path = self._path("kaputt.pdf")
        path.write_bytes(b"%PDF-1.7 kaputt")
        self.assertEqual(page_layout.extract_page_layout(path), [])

    def test_rotierte_seite_wird_ins_anzeige_koordinatensystem_transformiert(self):
        # /Rotate 90: page.rect liefert getauschte Anzeige-Maße; die Wortkästen
        # müssen per Rotationsmatrix ins selbe (Anzeige-)System überführt werden,
        # sonst säße das Overlay verdreht auf der von pdf.js gerenderten Seite.
        import fitz

        path = self._path("rot90.pdf")
        doc = fitz.open()
        page = doc.new_page(width=200, height=300)
        page.insert_text((20, 40), "TAG")
        page.set_rotation(90)
        doc.save(str(path))
        doc.close()

        pages = page_layout.extract_page_layout(path)
        self.assertEqual(len(pages), 1)
        p = pages[0]
        # 90°-Rotation vertauscht Breite/Höhe (Anzeige-Maße).
        self.assertEqual((p["width"], p["height"]), (300.0, 200.0))

        # Erwartete Box = Rohkasten * Rotationsmatrix (gerundet) – und NICHT die
        # un-rotierten Rohkoordinaten.
        d = fitz.open(str(path))
        raw = d[0].get_text("words")[0]
        r = fitz.Rect(raw[:4]) * d[0].rotation_matrix
        r.normalize()
        d.close()
        expected = [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]
        self.assertEqual(p["words"][0]["bbox"], expected)
        self.assertNotEqual(
            p["words"][0]["bbox"], [round(float(v), 2) for v in raw[:4]]
        )

    def test_gesamtlimit_wird_seitenuebergreifend_eingehalten(self):
        # Konstanten klein patchen: Gesamt 3, pro Seite 2. Zwei Seiten mit je 2
        # Wörtern dürfen zusammen NICHT über 3 kommen (Seite 1 → 2, Seite 2 → 1).
        from unittest.mock import patch

        path = self._path("viele.pdf")
        _text_pdf(path, ["Alpha Beta", "Gamma Delta"])

        with patch.object(page_layout, "MAX_WORDS_TOTAL", 3), patch.object(
            page_layout, "MAX_WORDS_PER_PAGE", 2
        ):
            pages = page_layout.extract_page_layout(path)

        total = sum(len(p["words"]) for p in pages)
        self.assertEqual(total, 3)
        self.assertEqual(len(pages[0]["words"]), 2)
        self.assertEqual(len(pages[1]["words"]), 1)


class WritePageLayoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="layout", password="pw12345!")
        self.doc = Document.objects.create(title="doc", owner=self.user)
        self.version = DocumentVersion.objects.create(
            document=self.doc, version_no=1, file_path="/x.pdf", sha256="a" * 64
        )

    def test_schreibt_und_ersetzt_idempotent(self):
        pages = [
            {
                "page_no": 1,
                "width": 595.0,
                "height": 842.0,
                "words": [{"t": "A", "bbox": [1, 2, 3, 4]}],
            }
        ]
        self.assertEqual(page_layout.write_page_layout(self.version, pages), 1)
        self.assertEqual(
            DocumentPageLayout.objects.filter(version=self.version).count(), 1
        )

        # Zweiter Lauf mit anderem Inhalt ersetzt vollständig (keine Dubletten).
        pages2 = [
            {"page_no": 1, "width": 595.0, "height": 842.0, "words": [{"t": "B", "bbox": [0, 0, 1, 1]}]},
            {"page_no": 2, "width": 595.0, "height": 842.0, "words": [{"t": "C", "bbox": [0, 0, 1, 1]}]},
        ]
        self.assertEqual(page_layout.write_page_layout(self.version, pages2), 2)
        rows = list(
            DocumentPageLayout.objects.filter(version=self.version).order_by("page_no")
        )
        self.assertEqual([r.page_no for r in rows], [1, 2])
        self.assertEqual(rows[0].words[0]["t"], "B")

    def test_seiten_ohne_woerter_werden_ausgelassen(self):
        pages = [{"page_no": 1, "width": 1, "height": 1, "words": []}]
        self.assertEqual(page_layout.write_page_layout(self.version, pages), 0)


class PageLayoutEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="owner", password="pw12345!")
        self.other = User.objects.create_user(username="fremd", password="pw12345!")
        self.doc = Document.objects.create(title="doc", owner=self.user)
        self.version = DocumentVersion.objects.create(
            document=self.doc, version_no=1, file_path="/x.pdf", sha256="b" * 64,
            page_count=2,
        )
        self.doc.current_version = self.version
        self.doc.save(update_fields=["current_version"])
        DocumentPageLayout.objects.create(
            version=self.version,
            page_no=1,
            width=595.0,
            height=842.0,
            words=[
                {"t": "Rechnung", "bbox": [72, 72, 140, 84]},
                {"t": "Betrag", "bbox": [72, 90, 130, 102]},
            ],
        )
        DocumentPageLayout.objects.create(
            version=self.version, page_no=2, width=595.0, height=842.0,
            words=[{"t": "Seite2", "bbox": [1, 2, 3, 4]}],
        )

    def test_uebersicht_liefert_nur_metadaten_ohne_woerter(self):
        # Default: klein – Seitenmaße + word_count, KEINE Wortlisten (Mobile-Payload).
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/{self.doc.id}/page-layout/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Versions-Identität eindeutig: DB-PK und fachliche Nummer getrennt.
        self.assertEqual(data["version_id"], self.version.id)
        self.assertEqual(data["version_no"], 1)
        self.assertNotIn("version", data)
        self.assertEqual(len(data["pages"]), 2)
        self.assertEqual(data["pages"][0]["word_count"], 2)
        self.assertNotIn("words", data["pages"][0])

    def test_einzelseite_liefert_volle_wortliste(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/{self.doc.id}/page-layout/?page=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertNotIn("pages", data)
        self.assertEqual(data["page"]["page_no"], 1)
        self.assertEqual(data["page"]["words"][0]["t"], "Rechnung")

    def test_unbekannte_seite_ist_404(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/{self.doc.id}/page-layout/?page=99")
        self.assertEqual(resp.status_code, 404)

    def test_ungueltige_seitennummer_ist_404(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/{self.doc.id}/page-layout/?page=abc")
        self.assertEqual(resp.status_code, 404)

    def test_fremdes_dokument_ist_nicht_sichtbar(self):
        self.client.force_authenticate(self.other)
        resp = self.client.get(f"/api/documents/{self.doc.id}/page-layout/")
        self.assertIn(resp.status_code, (403, 404))

    def test_suche_liefert_treffer_seitenuebergreifend(self):
        self.client.force_authenticate(self.user)
        # "Rechnung" (S.1), "Betrag" (S.1), "Seite2" (S.2) sind angelegt.
        resp = self.client.get(f"/api/documents/{self.doc.id}/page-layout/?term=betrag")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertNotIn("pages", data)
        self.assertEqual(data["total"], 1)
        self.assertFalse(data["truncated"])
        hit = data["matches"][0]
        self.assertEqual(hit["page_no"], 1)
        self.assertEqual(hit["t"], "Betrag")
        self.assertEqual(hit["bbox"], [72, 90, 130, 102])
        self.assertEqual(hit["width"], 595.0)

    def test_suche_ohne_treffer_ist_leer(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/{self.doc.id}/page-layout/?term=xyz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total"], 0)


class SearchLayoutServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="s", password="pw12345!")
        self.doc = Document.objects.create(title="doc", owner=self.user)
        self.version = DocumentVersion.objects.create(
            document=self.doc, version_no=1, file_path="/x.pdf", sha256="e" * 64
        )
        DocumentPageLayout.objects.create(
            version=self.version, page_no=1, width=100, height=100,
            words=[{"t": "Müller", "bbox": [1, 2, 3, 4]}, {"t": "Rechnung", "bbox": [5, 6, 7, 8]}],
        )
        DocumentPageLayout.objects.create(
            version=self.version, page_no=2, width=100, height=100,
            words=[{"t": "MÜLLER", "bbox": [9, 9, 9, 9]}],
        )

    def test_case_und_diakritika_tolerant_seitenuebergreifend(self):
        matches, truncated = page_layout.search_layout(self.version, "muller")
        self.assertFalse(truncated)
        self.assertEqual([m["page_no"] for m in matches], [1, 2])

    def test_leere_suche_liefert_nichts(self):
        self.assertEqual(page_layout.search_layout(self.version, "   "), ([], False))

    def test_limit_setzt_truncated(self):
        matches, truncated = page_layout.search_layout(self.version, "muller", limit=1)
        self.assertEqual(len(matches), 1)
        self.assertTrue(truncated)

    def test_wortfolge_ueber_mehrere_woerter_mit_union_box(self):
        # „Wien Energie" ist im OCR auf zwei Wörter verteilt.
        v = DocumentVersion.objects.create(
            document=self.doc, version_no=2, file_path="/w.pdf", sha256="f" * 64,
        )
        DocumentPageLayout.objects.create(
            version=v, page_no=1, width=100, height=100,
            words=[
                {"t": "Wien", "bbox": [10, 20, 30, 40]},
                {"t": "Energie", "bbox": [32, 20, 60, 40]},
            ],
        )
        matches, _ = page_layout.search_layout(v, "wien energie")
        self.assertEqual(len(matches), 1)
        # Union-Box umschließt beide Wörter.
        self.assertEqual(matches[0]["bbox"], [10.0, 20.0, 60.0, 40.0])
        self.assertEqual(matches[0]["t"], "Wien Energie")

    def test_iban_mit_leerzeichen_und_bloecken(self):
        v = DocumentVersion.objects.create(
            document=self.doc, version_no=3, file_path="/i.pdf", sha256="g" * 64,
        )
        DocumentPageLayout.objects.create(
            version=v, page_no=1, width=100, height=100,
            words=[
                {"t": "AT61", "bbox": [10, 20, 25, 30]},
                {"t": "1904", "bbox": [27, 20, 42, 30]},
                {"t": "3002", "bbox": [44, 20, 59, 30]},
            ],
        )
        matches, _ = page_layout.search_layout(v, "AT61 1904 3002")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["bbox"], [10.0, 20.0, 59.0, 30.0])


class PageLayoutSharingTests(TestCase):
    """Regressionsschutz: Haushaltsmitglieder sehen das Overlay geteilter Dokumente.

    Die Action muss in ``SAFE_READ_ACTIONS`` stehen – sonst 404 für Mitglieder,
    obwohl sie Vorschau/Dokument lesen dürfen (Phase 2 verlöre bei geteilten
    Dokumenten das OCR-Overlay).
    """

    def setUp(self):
        from accounts.models import Household

        self.client = APIClient()
        self.alice = User.objects.create_user("alice_pl", password="pw", role="user")
        self.bob = User.objects.create_user("bob_pl", password="pw", role="user")
        self.carol = User.objects.create_user("carol_pl", password="pw", role="user")
        household = Household.objects.create(name="Familie", created_by=self.alice)
        household.members.add(self.alice, self.bob)  # carol NICHT im Haushalt

        self.doc = Document.objects.create(
            title="geteilt", owner=self.alice, shared_with_household=True
        )
        self.version = DocumentVersion.objects.create(
            document=self.doc, version_no=1, file_path="/s.pdf", sha256="d" * 64,
            page_count=1,
        )
        self.doc.current_version = self.version
        self.doc.save(update_fields=["current_version"])
        DocumentPageLayout.objects.create(
            version=self.version, page_no=1, width=595.0, height=842.0,
            words=[{"t": "Geteilt", "bbox": [1, 2, 3, 4]}],
        )

    def test_mitglied_sieht_overlay_des_geteilten_dokuments(self):
        self.client.force_authenticate(self.bob)
        meta = self.client.get(f"/api/documents/{self.doc.id}/page-layout/")
        self.assertEqual(meta.status_code, 200)
        self.assertEqual(meta.json()["pages"][0]["word_count"], 1)
        page = self.client.get(f"/api/documents/{self.doc.id}/page-layout/?page=1")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["page"]["words"][0]["t"], "Geteilt")

    def test_nichtmitglied_sieht_das_overlay_nicht(self):
        self.client.force_authenticate(self.carol)
        resp = self.client.get(f"/api/documents/{self.doc.id}/page-layout/")
        self.assertEqual(resp.status_code, 404)


class ExtractionAnchorFieldTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="anchor", password="pw12345!")
        self.doc = Document.objects.create(title="doc", owner=self.user)
        self.version = DocumentVersion.objects.create(
            document=self.doc, version_no=1, file_path="/x.pdf", sha256="c" * 64
        )

    def test_verankerung_speichert_bbox_und_version(self):
        cand = ExtractionCandidate.objects.create(
            document=self.doc,
            field=ExtractionCandidate.Field.AMOUNT,
            value="42,00 €",
            source_page=1,
            source_version=self.version,
            source_bbox=[10, 20, 30, 40],
        )
        cand.refresh_from_db()
        self.assertEqual(cand.source_bbox, [10, 20, 30, 40])
        self.assertEqual(cand.source_version_id, self.version.id)

    def test_verankerung_ist_optional(self):
        cand = ExtractionCandidate.objects.create(
            document=self.doc,
            field=ExtractionCandidate.Field.IBAN,
            value="DE00",
        )
        cand.refresh_from_db()
        self.assertIsNone(cand.source_bbox)
        self.assertIsNone(cand.source_version)

    def test_geloeschte_version_setzt_verankerung_auf_null(self):
        # SET_NULL: wird die verankerte Version entfernt, bleibt der Vorschlag
        # erhalten (Textinhalt weiterhin nützlich), nur die Geometrie verwaist.
        cand = ExtractionCandidate.objects.create(
            document=self.doc,
            field=ExtractionCandidate.Field.AMOUNT,
            value="1,00 €",
            source_version=self.version,
            source_bbox=[1, 1, 2, 2],
        )
        self.version.delete()
        cand.refresh_from_db()
        self.assertIsNone(cand.source_version)

    def test_serializer_gibt_bbox_ohne_version_als_null(self):
        # API-Vertrag: ohne source_version ist die Box bezuglos → serialisiert als
        # null, egal ob in der DB (nach SET_NULL) noch ein Box-Rest liegt.
        from .serializers import ExtractionCandidateSerializer

        cand = ExtractionCandidate.objects.create(
            document=self.doc,
            field=ExtractionCandidate.Field.AMOUNT,
            value="1,00 €",
            source_version=self.version,
            source_bbox=[1, 1, 2, 2],
        )
        # Mit Version: Box wird ausgeliefert, samt fachlicher Versionsnummer.
        data = ExtractionCandidateSerializer(cand).data
        self.assertEqual(data["source_bbox"], [1, 1, 2, 2])
        self.assertEqual(data["source_version_no"], self.version.version_no)
        # Version gelöscht (SET_NULL) → Box in DB bleibt, API liefert aber null.
        self.version.delete()
        cand.refresh_from_db()
        self.assertIsNotNone(cand.source_bbox)  # DB-Rest noch da
        self.assertIsNone(
            ExtractionCandidateSerializer(cand).data["source_bbox"]  # API null
        )
