"""Test der Versions-Rekonstruktion für historische Markierungen (Migration 0075).

0074 band alte Markierungen pauschal an current_version; 0075 rekonstruiert die
Version, die beim Anlegen der Markierung aktuell war (größte created_at <= der der
Markierung). Getestet wird die Rekonstruktionsfunktion direkt.
"""
import importlib
from datetime import datetime, timezone as dt_tz

from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from documents.models import Document, DocumentHighlight, DocumentVersion

User = get_user_model()


def _reconstruct():
    # Migrationsmodul dynamisch laden (Dateiname beginnt mit Ziffer → kein normaler Import).
    mod = importlib.import_module(
        "documents.migrations.0075_highlight_version_by_created_at"
    )
    mod.reconstruct_version(global_apps, None)


class HighlightVersionReconstructTests(TestCase):
    def _version(self, doc, no, when):
        v = DocumentVersion.objects.create(
            document=doc, version_no=no, file_path=f"/v{no}.pdf", sha256=str(no) * 64
        )
        DocumentVersion.objects.filter(pk=v.pk).update(created_at=when)
        v.refresh_from_db()
        return v

    def test_bindet_an_zur_anlagezeit_aktuelle_version(self):
        user = User.objects.create_user("u", password="pw12345!")
        doc = Document.objects.create(title="doc", owner=user)
        v1 = self._version(doc, 1, datetime(2026, 1, 1, tzinfo=dt_tz.utc))
        v2 = self._version(doc, 2, datetime(2026, 6, 1, tzinfo=dt_tz.utc))
        doc.current_version = v2
        doc.save(update_fields=["current_version"])

        # Markierung wurde im März angelegt (v1 war aktuell), 0074 band sie aber an v2.
        hl = DocumentHighlight.objects.create(
            document=doc, version=v2, page_no=1, bbox=[1, 2, 3, 4], created_by=user
        )
        DocumentHighlight.objects.filter(pk=hl.pk).update(
            created_at=datetime(2026, 3, 1, tzinfo=dt_tz.utc)
        )

        _reconstruct()

        hl.refresh_from_db()
        self.assertEqual(hl.version_id, v1.id)  # korrekt auf v1 zurückgebunden

    def test_korrekt_gebundene_bleibt_unangetastet(self):
        user = User.objects.create_user("u2", password="pw12345!")
        doc = Document.objects.create(title="doc2", owner=user)
        v1 = self._version(doc, 1, datetime(2026, 1, 1, tzinfo=dt_tz.utc))
        hl = DocumentHighlight.objects.create(
            document=doc, version=v1, page_no=1, bbox=[1, 2, 3, 4], created_by=user
        )
        DocumentHighlight.objects.filter(pk=hl.pk).update(
            created_at=datetime(2026, 2, 1, tzinfo=dt_tz.utc)
        )
        _reconstruct()
        hl.refresh_from_db()
        self.assertEqual(hl.version_id, v1.id)
