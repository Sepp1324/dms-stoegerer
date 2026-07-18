"""Tests: materialisierter Suchvektor wird gepflegt (Perf, P2 Teil 5a).

Prüft, dass die Spalte über Service/Signale/Backfill gefüllt wird und per
gespeichertem Vektor (nicht query-time) durchsuchbar ist. Die Such-VIEW bleibt
in 5a unverändert (query-time) – das Umstellen folgt in 5b.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.postgres.search import SearchQuery
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APITestCase

from .models import Document, DocumentVersion, Tag
from .services.search_vector import update_document_search_vector


def _matches(doc_id: int, term: str) -> bool:
    return Document.objects.filter(
        pk=doc_id, search_vector=SearchQuery(term, config="german")
    ).exists()


class SearchVectorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("sv", password="pw12345!")

    def test_update_populates_and_indexes_ocr_and_title(self):
        doc = Document.objects.create(title="Stromrechnung", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path="/tmp/x",
            sha256="a" * 8,
            ocr_text="Jahresabrechnung Kilowattstunden Netzentgelt",
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        update_document_search_vector(doc)
        doc.refresh_from_db()
        self.assertIsNotNone(doc.search_vector)
        self.assertTrue(_matches(doc.pk, "Stromrechnung"))  # Titel (A)
        self.assertTrue(_matches(doc.pk, "Netzentgelt"))  # OCR-Text (D)

    def test_post_save_signal_fills_vector_on_create(self):
        doc = Document.objects.create(title="Zahnarztrechnung", owner=self.user)
        doc.refresh_from_db()
        self.assertIsNotNone(doc.search_vector)
        self.assertTrue(_matches(doc.pk, "Zahnarztrechnung"))

    def test_tag_change_updates_vector(self):
        doc = Document.objects.create(title="Doc", owner=self.user)
        tag = Tag.objects.create(name="Versicherung")
        doc.tags.add(tag)  # m2m_changed post_add -> Vektor-Refresh
        self.assertTrue(_matches(doc.pk, "Versicherung"))

    def test_backfill_command_fills_null_vectors(self):
        doc = Document.objects.create(title="Backfilltest", owner=self.user)
        # Vor-Backfill-Zustand simulieren (Signal hat es schon gefüllt -> leeren).
        Document.objects.filter(pk=doc.pk).update(search_vector=None)
        call_command("backfill_search_vectors")
        doc.refresh_from_db()
        self.assertIsNotNone(doc.search_vector)
        self.assertTrue(_matches(doc.pk, "Backfilltest"))


class SearchUsesStoredVectorTests(APITestCase):
    """5b: Die Such-View trifft ausschließlich über die gespeicherte Spalte.

    Beweis: Ein Dokument mit OCR-Treffer, dessen ``search_vector`` künstlich auf
    NULL gesetzt ist, taucht NICHT auf – die alte Query-Zeit-Logik hätte es
    gefunden.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user("sv2", password="pw12345!")
        self.client.force_authenticate(self.user)

    def _doc_with_ocr(self, title, ocr):
        doc = Document.objects.create(title=title, owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path="/tmp/x",
            sha256=f"{doc.id:064d}",
            ocr_text=ocr,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])  # Signal füllt Vektor inkl. OCR
        return doc

    def test_populated_vector_is_found(self):
        doc = self._doc_with_ocr("Beleg", "Rechnungsbetrag Sonderzeichenkennung")
        resp = self.client.get("/api/documents/?q=Sonderzeichenkennung")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(doc.id, [r["id"] for r in resp.data["results"]])

    def test_null_vector_is_not_found(self):
        doc = self._doc_with_ocr("Beleg", "Rechnungsbetrag Sonderzeichenkennung")
        Document.objects.filter(pk=doc.pk).update(search_vector=None)
        resp = self.client.get("/api/documents/?q=Sonderzeichenkennung")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(doc.id, [r["id"] for r in resp.data["results"]])
