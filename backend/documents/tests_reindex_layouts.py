"""Test: reindex_page_layouts löscht bei unerreichbarer Quelle nichts.

Liefert die Extraktion (z. B. bei NFS-Ausfall) eine leere Liste, darf der Backfill
KEIN bestehendes Layout löschen – sonst wischt ein --all-Lauf alle Studio-Daten weg.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from documents.models import Document, DocumentPageLayout, DocumentVersion

User = get_user_model()


class ReindexPageLayoutsGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", password="pw12345!")
        self.doc = Document.objects.create(title="doc", owner=self.user)
        self.version = DocumentVersion.objects.create(
            document=self.doc, version_no=1, file_path="/x.pdf", sha256="a" * 64
        )
        DocumentPageLayout.objects.create(
            version=self.version, page_no=1, width=100, height=100,
            words=[{"t": "Bestand", "bbox": [1, 2, 3, 4]}],
        )

    def test_leere_extraktion_laesst_bestehendes_layout_unangetastet(self):
        with patch(
            "documents.services.page_layout.extract_page_layout", return_value=[]
        ):
            call_command("reindex_page_layouts", "--all")
        # Das vorhandene Layout ist NICHT gelöscht.
        self.assertTrue(
            DocumentPageLayout.objects.filter(version=self.version).exists()
        )

    def test_neue_extraktion_ersetzt(self):
        with patch(
            "documents.services.page_layout.extract_page_layout",
            return_value=[
                {"page_no": 1, "width": 200, "height": 300,
                 "words": [{"t": "Neu", "bbox": [5, 6, 7, 8]}]},
            ],
        ):
            call_command("reindex_page_layouts", "--all")
        rows = DocumentPageLayout.objects.filter(version=self.version)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().words[0]["t"], "Neu")
