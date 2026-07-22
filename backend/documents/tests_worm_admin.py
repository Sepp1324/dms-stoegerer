"""P1: WORM/Retention/Legal Hold dürfen NICHT über den Django-Admin umgangen
werden. Der Admin löscht über den Collector (umgeht model.delete()), daher ist
die Löschung dort komplett gesperrt; zusätzlich schützt Document.delete() den
programmatischen Pfad."""
from datetime import date, timedelta

from django.contrib import admin as djadmin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from .admin import DocumentAdmin, DocumentVersionAdmin
from .models import Document, DocumentVersion

User = get_user_model()


def _version(doc, *, immutable=False, retention=None, no=1):
    return DocumentVersion.objects.create(
        document=doc, version_no=no, file_path=f"/tmp/v{no}.pdf",
        sha256=f"{no:064d}", is_immutable=immutable, retention_until=retention,
    )


class AdminDeleteLockTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user("root", password="pw", role="admin")

    def _req(self):
        req = self.factory.get("/admin/")
        req.user = self.user
        return req

    def test_document_admin_delete_gesperrt(self):
        ma = DocumentAdmin(Document, djadmin.site)
        self.assertFalse(ma.has_delete_permission(self._req()))
        # Massenaktion „delete_selected" ist entfernt:
        self.assertNotIn("delete_selected", ma.get_actions(self._req()))

    def test_version_admin_delete_gesperrt(self):
        ma = DocumentVersionAdmin(DocumentVersion, djadmin.site)
        self.assertFalse(ma.has_delete_permission(self._req()))
        self.assertNotIn("delete_selected", ma.get_actions(self._req()))


class DocumentDeleteGuardTests(TestCase):
    def test_unveraenderliche_version_sperrt_loeschen(self):
        doc = Document.objects.create(title="WORM")
        _version(doc, immutable=True)
        with self.assertRaises(ValidationError):
            doc.delete()
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_legal_hold_sperrt_loeschen(self):
        doc = Document.objects.create(title="Hold", legal_hold=True)
        with self.assertRaises(ValidationError):
            doc.delete()
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())

    def test_aufbewahrung_sperrt_loeschen(self):
        doc = Document.objects.create(
            title="Retention", retention_until=date.today() + timedelta(days=5)
        )
        with self.assertRaises(ValidationError):
            doc.delete()

    def test_versions_aufbewahrung_sperrt_loeschen(self):
        doc = Document.objects.create(title="VerRetention")
        _version(doc, retention=date.today() + timedelta(days=3))
        with self.assertRaises(ValidationError):
            doc.delete()

    def test_ungeschuetztes_dokument_wird_geloescht(self):
        doc = Document.objects.create(title="Frei")
        _version(doc, immutable=False)
        doc.delete()
        self.assertFalse(Document.objects.filter(pk=doc.pk).exists())
