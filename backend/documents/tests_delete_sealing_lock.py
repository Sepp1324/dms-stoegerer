"""P1: Document.delete() wertet den Schutzstatus UNTER Zeilensperre aus.

Der eigentliche Race (paralleles Sealing zwischen Prüfung und Löschen) wird durch
``select_for_update()`` auf Dokument UND Versionen serialisiert (analog zu
``seal_version``). Hier deterministisch: eine bereits unveränderliche Version
blockt das Löschen weiterhin (der gesperrte Pfad wertet delete_block korrekt aus).
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

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
