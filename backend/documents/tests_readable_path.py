"""P2: Der gemeinsame Lese-Fallback (Archiv -> Original) gilt zentral für alle
Leser (Thumbnail, Reindex, Vorschau), nicht nur die Vorschau."""
import os
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from documents import pipeline
from documents.models import Document, DocumentVersion


class ResolveReadableVersionPathTests(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _version(self, *, archive_rel=None, file_rel="orig.pdf", write_archive=True,
                 write_file=True, ocr_text=""):
        archive = str(self.tmp / archive_rel) if archive_rel else ""
        file_path = str(self.tmp / file_rel) if file_rel else ""
        if archive and write_archive:
            Path(archive).write_bytes(b"%PDF archive")
        if file_path and write_file:
            Path(file_path).write_bytes(b"%PDF original text")
        doc = Document.objects.create(title="D")
        v = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path=file_path,
            archive_path=archive, sha256="a" * 64, ocr_text=ocr_text,
        )
        doc.current_version = v
        doc.save(update_fields=["current_version"])
        return v

    def test_bevorzugt_archiv(self):
        v = self._version(archive_rel="a.pdf")
        self.assertEqual(pipeline.resolve_readable_version_path(v), v.archive_path)

    def test_fallback_auf_original_wenn_archiv_fehlt(self):
        v = self._version(archive_rel="a.pdf", write_archive=False)
        self.assertEqual(pipeline.resolve_readable_version_path(v), v.file_path)

    def test_ohne_archiv_nutzt_original(self):
        v = self._version(archive_rel=None)
        self.assertEqual(pipeline.resolve_readable_version_path(v), v.file_path)

    def test_beide_fehlen_none(self):
        v = self._version(archive_rel="a.pdf", write_archive=False, write_file=False)
        self.assertIsNone(pipeline.resolve_readable_version_path(v))

    def test_reindex_nutzt_original_wenn_archiv_fehlt(self):
        # Archiv gesetzt, aber verschwunden; Original vorhanden -> darf NICHT
        # uebersprungen werden, sondern der Volltext aus dem ORIGINAL kommen.
        from unittest import mock

        v = self._version(archive_rel="a.pdf", write_archive=False, ocr_text="")
        with mock.patch(
            "documents.pipeline.extract_text", side_effect=lambda p: f"TEXT::{p}"
        ):
            call_command("reindex_text", stdout=StringIO())
        v.refresh_from_db()
        self.assertEqual(v.ocr_text, f"TEXT::{v.file_path}")  # Original, nicht Archiv

    def test_reindex_page_texts_nutzt_original_wenn_archiv_fehlt(self):
        # Archiv gesetzt, aber verschwunden -> extract_page_texts muss mit dem
        # ORIGINAL aufgerufen werden (sonst landet der ganze OCR-Text als 1 Seite).
        from unittest import mock

        v = self._version(archive_rel="a.pdf", write_archive=False, ocr_text="x")
        seen = {}
        with mock.patch(
            "documents.services.page_text.extract_page_texts",
            side_effect=lambda src, fallback_text="": seen.setdefault("src", src) or [],
        ), mock.patch(
            "documents.services.page_text.write_page_texts", return_value=1
        ):
            call_command("reindex_page_texts", "--all", stdout=StringIO())
        self.assertEqual(seen["src"], v.file_path)   # Original, nicht das fehlende Archiv
