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
            page_count=1,
        )
        self.doc.current_version = self.version
        self.doc.save(update_fields=["current_version"])
        DocumentPageLayout.objects.create(
            version=self.version,
            page_no=1,
            width=595.0,
            height=842.0,
            words=[{"t": "Rechnung", "bbox": [72, 72, 140, 84]}],
        )

    def test_owner_erhaelt_layout(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(f"/api/documents/{self.doc.id}/page-layout/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["pages"]), 1)
        self.assertEqual(data["pages"][0]["words"][0]["t"], "Rechnung")

    def test_fremdes_dokument_ist_nicht_sichtbar(self):
        self.client.force_authenticate(self.other)
        resp = self.client.get(f"/api/documents/{self.doc.id}/page-layout/")
        self.assertIn(resp.status_code, (403, 404))


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
