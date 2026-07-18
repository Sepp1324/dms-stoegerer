"""Tests: Versionsnummern werden korrekt fortlaufend vergeben (P2).

Die eigentliche Race-Absicherung ist strukturell (``select_for_update`` auf die
Document-Zeile in ``create_version_for_document`` + ``unique_together`` als
DB-Netz) und lässt sich in Djangos transaktions-gekapseltem ``TestCase`` nicht
verlässlich (nicht-flaky) über Threads nachstellen. Dieser Test sichert die
funktionale Korrektheit des gesperrten Pfads: aufeinanderfolgende Versionen
erhalten 2, 3, … und ``current_version`` zeigt auf die neueste.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import pipeline


class VersionSequentialTests(TestCase):
    def test_versions_are_numbered_sequentially(self):
        owner = get_user_model().objects.create_user("vr", password="pw12345!")
        # size explizit → kein Zugriff auf eine echte Datei nötig.
        document, v1 = pipeline.create_document_from_file(
            "/tmp/a.pdf", title="D", owner=owner, size=10
        )
        self.assertEqual(v1.version_no, 1)

        v2 = pipeline.create_version_for_document(
            document, "/tmp/b.pdf", created_by=owner, size=10
        )
        v3 = pipeline.create_version_for_document(
            document, "/tmp/c.pdf", created_by=owner, size=10
        )
        self.assertEqual([v2.version_no, v3.version_no], [2, 3])

        document.refresh_from_db()
        self.assertEqual(document.current_version_id, v3.id)
