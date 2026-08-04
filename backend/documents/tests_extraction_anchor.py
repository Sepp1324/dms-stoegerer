"""Tests für die Studio-Verankerung der Extraktionskandidaten (Phase 2).

``generate_candidates`` setzt jetzt zusätzlich ``source_version`` + ``source_bbox``,
wenn der extrahierte Wert im wortgenauen OCR-Layout der aktuellen Version gefunden
wird. Best-effort: ohne Layout/Treffer bleibt der Vorschlag ohne Geometrie.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (
    Document,
    DocumentPageLayout,
    DocumentPageText,
    DocumentVersion,
    ExtractionCandidate,
)
from .services import extraction

User = get_user_model()


class FindBboxTests(TestCase):
    def test_einzelwort_treffer(self):
        words = [{"t": "01.02.2023", "bbox": [10, 20, 60, 30]}]
        self.assertEqual(
            extraction._find_bbox(words, "01.02.2023"), [10.0, 20.0, 60.0, 30.0]
        )

    def test_mehrwort_union_iban_in_bloecken(self):
        # IBAN im OCR in 4er-Blöcke getrennt → Union über die beteiligten Wörter.
        words = [
            {"t": "AT61", "bbox": [10, 20, 30, 30]},
            {"t": "1904", "bbox": [32, 20, 52, 30]},
            {"t": "3002", "bbox": [54, 20, 74, 30]},
            {"t": "Rest", "bbox": [80, 40, 90, 50]},
        ]
        self.assertEqual(
            extraction._find_bbox(words, "AT61 1904 3002"),
            [10.0, 20.0, 74.0, 30.0],
        )

    def test_kein_treffer_liefert_none(self):
        words = [{"t": "Hallo", "bbox": [1, 2, 3, 4]}]
        self.assertIsNone(extraction._find_bbox(words, "Weltfrieden"))

    def test_leerer_wert_liefert_none(self):
        self.assertIsNone(extraction._find_bbox([{"t": "x", "bbox": [1, 2, 3, 4]}], ""))


class GenerateCandidatesAnchorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="a", password="pw12345!")
        self.doc = Document.objects.create(title="doc", owner=self.user)
        self.version = DocumentVersion.objects.create(
            document=self.doc, version_no=1, file_path="/x.pdf", sha256="f" * 64,
            page_count=1,
        )
        self.doc.current_version = self.version
        self.doc.save(update_fields=["current_version"])
        DocumentPageText.objects.create(
            version=self.version, page_no=1, text="Rechnungsbetrag: 42,00 EUR"
        )

    def _amount(self):
        return self.doc.extraction_candidates.filter(
            field=ExtractionCandidate.Field.AMOUNT
        ).first()

    def test_kandidat_wird_verankert(self):
        DocumentPageLayout.objects.create(
            version=self.version, page_no=1, width=595.0, height=842.0,
            words=[
                {"t": "42,00", "bbox": [10, 20, 40, 30]},
                {"t": "EUR", "bbox": [45, 20, 60, 30]},
            ],
        )
        extraction.generate_candidates(self.doc)
        cand = self._amount()
        self.assertIsNotNone(cand)
        self.assertEqual(cand.source_version_id, self.version.id)
        self.assertEqual(cand.source_bbox, [10.0, 20.0, 60.0, 30.0])

    def test_ohne_layout_bleibt_ohne_geometrie(self):
        # Kein DocumentPageLayout → Kandidat entsteht trotzdem, nur ohne Anker.
        extraction.generate_candidates(self.doc)
        cand = self._amount()
        self.assertIsNotNone(cand)
        self.assertIsNone(cand.source_version)
        self.assertIsNone(cand.source_bbox)
