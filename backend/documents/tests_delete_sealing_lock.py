"""P1: Document.delete() wertet den Schutzstatus UNTER Zeilensperre aus.

Der eigentliche Race (paralleles Sealing zwischen Prüfung und Löschen) wird durch
``select_for_update()`` auf Dokument UND Versionen serialisiert (analog zu
``seal_version``). Hier deterministisch: eine bereits unveränderliche Version
blockt das Löschen weiterhin (der gesperrte Pfad wertet delete_block korrekt aus).
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from .models import AuditLogEntry, Document, DocumentVersion

User = get_user_model()


class DeleteUnderLockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("del_lock", password="pw", role="user")

    def _doc_with_version(self, *, immutable):
        doc = Document.objects.create(title="Doc", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path="/tmp/x.pdf",
            sha256="a" * 64, mime_type="application/pdf", size=1,
            is_immutable=immutable,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_worm_version_blockt_loeschen_unter_lock(self):
        doc = self._doc_with_version(immutable=True)
        with self.assertRaises(ValidationError):
            doc.delete()
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
        self.assertTrue(
            AuditLogEntry.objects.filter(
                action="immutable_block", object_id=str(doc.id)
            ).exists()
        )

    def test_loeschbares_dokument_wird_unter_lock_geloescht(self):
        doc = self._doc_with_version(immutable=False)
        doc.delete()
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())

    def test_lock_reihenfolge_dokument_vor_version(self):
        """Deadlock-Schutz (P1): delete() sperrt SYSTEMWEIT in der Reihenfolge
        Document → DocumentVersion – erst das Dokument, dann die Versionen. Identisch
        zu add_version (sperrt das Dokument, bevor es eine Version anlegt) und
        seal_version. Gegenläufige Reihenfolge ergäbe einen PostgreSQL-Deadlock und
        ließe add_version eine unerfasste neue Version einschieben.
        """
        if connection.vendor != "postgresql":
            self.skipTest("FOR UPDATE nur unter PostgreSQL aussagekräftig")
        doc = self._doc_with_version(immutable=False)
        with CaptureQueriesContext(connection) as ctx:
            doc.delete()
        for_update = [
            q["sql"].lower()
            for q in ctx.captured_queries
            if "for update" in q["sql"].lower()
        ]
        document_first = next(
            (
                i
                for i, s in enumerate(for_update)
                if "documentversion" not in s and "documents_document" in s
            ),
            None,
        )
        version_first = next(
            (i for i, s in enumerate(for_update) if "documentversion" in s), None
        )
        self.assertIsNotNone(document_first, "Dokumentzeile wurde nicht gesperrt")
        self.assertIsNotNone(version_first, "Versionszeile wurde nicht gesperrt")
        self.assertLess(
            document_first,
            version_first,
            "delete() muss das Dokument VOR den Versionen sperren (Document→Version)",
        )
