"""Query-Budget-Test: Die Dokumentliste skaliert NICHT linear mit der Doku-Zahl.

Regression gegen N+1 (Owner, Versionsersteller, Ordner-Eltern u. a.): Dank
``select_related``/``prefetch_related`` im ViewSet-Queryset darf das Hinzufügen
weiterer Dokumente auf derselben Seite die Query-Zahl praktisch nicht erhöhen.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from .models import Document, DocumentFolder, DocumentVersion


class ListQueryBudgetTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("qb", password="pw12345!")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        root = DocumentFolder.objects.create(name="Root")
        self.folder = DocumentFolder.objects.create(name="Sub", parent=root)
        self._n = 0

    def _make_docs(self, count):
        for _ in range(count):
            self._n += 1
            doc = Document.objects.create(
                title=f"D{self._n}", owner=self.user, folder=self.folder
            )
            version = DocumentVersion.objects.create(
                document=doc,
                version_no=1,
                file_path="/tmp/x",
                sha256=f"{self._n:064d}",
                created_by=self.user,
            )
            doc.current_version = version
            doc.save(update_fields=["current_version"])

    def test_list_does_not_scale_queries_with_document_count(self):
        self._make_docs(2)
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(self.client.get("/api/documents/").status_code, 200)

        self._make_docs(4)  # jetzt 6 Dokumente (alle auf einer Seite, PAGE_SIZE 25)
        with CaptureQueriesContext(connection) as large:
            self.assertEqual(self.client.get("/api/documents/").status_code, 200)

        # Bei N+1 würde die Query-Zahl mit den 4 zusätzlichen Dokumenten deutlich
        # steigen. Mit Prefetch/Select-Related bleibt sie nahezu konstant – etwas
        # Slack für aggregierte Zählungen o. Ä.
        growth = len(large.captured_queries) - len(small.captured_queries)
        self.assertLessEqual(growth, 3, f"N+1-Verdacht: +{growth} Queries für +4 Dokumente")
