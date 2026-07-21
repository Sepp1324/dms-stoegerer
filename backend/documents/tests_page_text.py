"""Tests für den seitengenauen Textindex (``page_text``).

``extract_page_texts`` liest PDFs seitenweise (PyMuPDF) und fällt für Nicht-PDFs,
fehlende/defekte Dateien und textlose PDFs auf eine Einzelseite mit dem
OCR-Gesamttext zurück. ``write_page_texts`` ersetzt den Index einer Version.
"""
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import pipeline
from .models import Document, DocumentPageText, DocumentVersion
from .services import page_text

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


class ExtractPageTextsTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _path(self, name: str) -> Path:
        return Path(self.tmp.name) / name

    def test_pdf_wird_seitenweise_gelesen(self):
        path = self._path("zwei.pdf")
        _text_pdf(path, ["Seite eins Inhalt", "Seite zwei Inhalt"])

        pages = page_text.extract_page_texts(path)

        self.assertEqual([p["page_no"] for p in pages], [1, 2])
        self.assertIn("Seite eins", pages[0]["text"])
        self.assertIn("Seite zwei", pages[1]["text"])

    def test_textlose_seiten_werden_uebersprungen(self):
        # Seite 1 leer, Seite 2 mit Text -> nur Seite 2 (mit ihrer echten Nummer).
        path = self._path("gemischt.pdf")
        _text_pdf(path, ["", "Nur hier steht Text"])

        pages = page_text.extract_page_texts(path)

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["page_no"], 2)
        self.assertIn("Nur hier", pages[0]["text"])

    def test_pdf_ganz_ohne_text_faellt_auf_fallback(self):
        path = self._path("leer.pdf")
        _text_pdf(path, ["", ""])

        pages = page_text.extract_page_texts(path, fallback_text="OCR-Gesamttext")

        self.assertEqual(pages, [{"page_no": 1, "text": "OCR-Gesamttext"}])

    def test_nicht_pdf_faellt_auf_fallback(self):
        path = self._path("notiz.txt")
        path.write_text("egal", encoding="utf-8")

        pages = page_text.extract_page_texts(path, fallback_text="ersatz")

        self.assertEqual(pages, [{"page_no": 1, "text": "ersatz"}])

    def test_fehlende_datei_faellt_auf_fallback(self):
        pages = page_text.extract_page_texts(
            self._path("gibtsnicht.pdf"), fallback_text="ersatz"
        )
        self.assertEqual(pages, [{"page_no": 1, "text": "ersatz"}])

    def test_leerer_fallback_gibt_leere_liste(self):
        pages = page_text.extract_page_texts(self._path("weg.pdf"), fallback_text="  ")
        self.assertEqual(pages, [])


class WritePageTextsTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.user = User.objects.create_user(
            username="page-text", password="pw", role="user"
        )
        path = Path(self.tmp.name) / "doc.pdf"
        _text_pdf(path, ["x"])
        doc = Document.objects.create(title="doc", owner=self.user)
        self.version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=str(path),
            sha256=pipeline.sha256_of(path),
            mime_type="application/pdf",
            size=path.stat().st_size,
            created_by=self.user,
        )

    def test_schreibt_nur_seiten_mit_text(self):
        written = page_text.write_page_texts(
            self.version,
            [
                {"page_no": 1, "text": "eins"},
                {"page_no": 2, "text": "   "},  # leer -> wird verworfen
                {"page_no": 3, "text": "drei"},
            ],
        )
        self.assertEqual(written, 2)
        rows = DocumentPageText.objects.filter(version=self.version).order_by("page_no")
        self.assertEqual([r.page_no for r in rows], [1, 3])

    def test_ersetzt_bestehenden_index(self):
        page_text.write_page_texts(self.version, [{"page_no": 1, "text": "alt"}])
        page_text.write_page_texts(self.version, [{"page_no": 1, "text": "neu"}])
        rows = DocumentPageText.objects.filter(version=self.version)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().text, "neu")
