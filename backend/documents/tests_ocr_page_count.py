"""``version.page_count`` stammt aus dem PDF (pikepdf), nicht aus der groben
Zeilenumbruch-Schätzung von ``run_ocr``.

Regression: für Bild-Scans mit wenig/keinem Text lieferte ``run_ocr`` immer
``pages=1`` (``text.count("\\n") // 50``), sodass mehrseitige Scans in der UI als
1 Seite erschienen. ``ocr_version`` liest die echte Seitenzahl jetzt via
``_page_count`` und fällt nur zurück, wenn die Quelle kein lesbares PDF ist.
"""
import io
import tempfile
from pathlib import Path
from unittest import mock

import pikepdf
from django.contrib.auth import get_user_model
from django.test import TestCase

from . import pipeline
from .models import Document, DocumentVersion
from .services.ocr.types import OCRResult, OCRStatusEnum

User = get_user_model()


def _skipped_result(pages_guess: int) -> OCRResult:
    # run_ocr überspringt OCR (kein Archiv-PDF) und meldet die grobe Schätzung.
    return OCRResult(
        text="",
        pages=pages_guess,
        status=OCRStatusEnum.SKIPPED,
        duration_ms=1,
        engine="text-extraction",
    )


class OcrPageCountTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.user = User.objects.create_user(
            username="ocr-pages", password="pw", role="user"
        )

    def _hashed_version(self, path: Path) -> DocumentVersion:
        doc = Document.objects.create(title="scan", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=str(path),
            sha256=pipeline.sha256_of(path),
            mime_type="application/pdf",
            size=path.stat().st_size,
            page_count=0,
            created_by=self.user,
            processing_state=DocumentVersion.ProcessingState.HASHED,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return version

    def test_page_count_kommt_aus_pdf_nicht_aus_schaetzung(self):
        path = Path(self.tmp.name) / "scan3.pdf"
        pdf = pikepdf.Pdf.new()
        for _ in range(3):
            pdf.add_blank_page(page_size=(200, 200))
        pdf.save(path)
        pdf.close()

        version = self._hashed_version(path)
        # run_ocr "schätzt" 1 Seite – echte Seitenzahl ist 3.
        with mock.patch.object(
            pipeline, "run_ocr", return_value=_skipped_result(1)
        ):
            pipeline.ocr_version(version)

        version.refresh_from_db()
        self.assertEqual(version.page_count, 3)

    def test_fallback_auf_schaetzung_wenn_quelle_kein_pdf(self):
        # Bild-Original ohne erzeugtes Archiv-PDF: _page_count scheitert (kein
        # PDF) -> Fallback auf die run_ocr-Schätzung.
        from PIL import Image

        img_path = Path(self.tmp.name) / "scan.jpg"
        buf = io.BytesIO()
        Image.new("RGB", (60, 80), "white").save(buf, format="JPEG")
        img_path.write_bytes(buf.getvalue())

        version = self._hashed_version(img_path)
        with mock.patch.object(
            pipeline, "run_ocr", return_value=_skipped_result(1)
        ):
            pipeline.ocr_version(version)

        version.refresh_from_db()
        self.assertEqual(version.page_count, 1)
