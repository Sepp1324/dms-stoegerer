"""P2: Regelklassifizierung ist atomar und validiert ``then`` (Typen/Listen).

Deckt ab:
  * ``tags: "steuer"`` (String) wird NICHT zeichenweise zerlegt.
  * Nicht-String-Einzelwerte (z. B. eine Liste als document_type) werden verworfen.
  * Ein Fehler beim Anlegen der Stammdaten rollt ALLES zurück (keine
    Teil-Stammdaten, keine Klassifizierung, kein Audit).
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from documents.classification import apply_rules
from documents.models import (
    AuditLogEntry,
    ClassificationRule,
    Document,
    DocumentType,
    DocumentVersion,
    Tag,
)

User = get_user_model()


class ClassificationThenValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("then_u", password="pw", role="user")

    def _doc(self, text="steuer rechnung"):
        doc = Document.objects.create(title="Doc", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc, version_no=1, file_path="/tmp/x.pdf", ocr_text=text
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_tags_string_wird_nicht_zeichenweise_zerlegt(self):
        ClassificationRule.objects.create(
            name="R", enabled=True, owner=None,
            match={"text_contains": ["steuer"]},
            then={"tags": "steuer", "document_type": "Rechnung"},
        )
        doc = self._doc()
        apply_rules(doc)
        doc.refresh_from_db()

        # Der gültige Einzelwert greift ...
        self.assertEqual(doc.document_type.name, "Rechnung")
        # ... aber KEINE Ein-Zeichen-Tags (s, t, e, u, r) entstehen.
        self.assertEqual(doc.tags.count(), 0)
        self.assertFalse(Tag.objects.filter(name__in=list("steuer")).exists())

    def test_nicht_string_einzelwert_wird_verworfen(self):
        ClassificationRule.objects.create(
            name="R", enabled=True, owner=None,
            match={"text_contains": ["steuer"]},
            then={"document_type": ["Rechnung"]},  # Liste statt String
        )
        doc = self._doc()
        apply_rules(doc)
        doc.refresh_from_db()
        self.assertIsNone(doc.document_type)
        # Kein Müll-Typ '["Rechnung"]' angelegt.
        self.assertFalse(DocumentType.objects.filter(name="['Rechnung']").exists())

    def test_fehler_rollt_stammdaten_und_klassifizierung_zurueck(self):
        ClassificationRule.objects.create(
            name="R", enabled=True, owner=None,
            match={"text_contains": ["steuer"]},
            then={"document_type": "Rechnung", "tags": ["A", "B"]},
        )
        doc = self._doc()

        real = Tag.objects.get_or_create
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:  # zweites Tag scheitert
                raise RuntimeError("boom")
            return real(*args, **kwargs)

        with mock.patch.object(Tag.objects, "get_or_create", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                apply_rules(doc)

        doc.refresh_from_db()
        # Rollback: kein Typ, keine Tags, keine Klassifizierung, kein Audit.
        self.assertIsNone(doc.document_type)
        self.assertFalse(DocumentType.objects.filter(name="Rechnung").exists())
        self.assertEqual(doc.tags.count(), 0)
        self.assertFalse(doc.classification)
        self.assertFalse(
            AuditLogEntry.objects.filter(
                action="classify", object_id=str(doc.id)
            ).exists()
        )
