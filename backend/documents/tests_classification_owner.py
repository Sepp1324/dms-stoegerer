"""P1: Klassifizierungsregeln sind owner-gescopt – eine Regel wirkt nur auf
Dokumente ihres Owners (oder global bei owner=null)."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from documents.classification import apply_rules
from documents.models import ClassificationRule, Document

User = get_user_model()


class ClassificationOwnerScopingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("cr_alice", password="pw", role="user")
        cls.bob = User.objects.create_user("cr_bob", password="pw", role="user")

    def _doc(self, owner):
        return Document.objects.create(title="Meine Rechnung", owner=owner)

    def _rule(self, owner):
        return ClassificationRule.objects.create(
            name=f"R-{owner or 'global'}", enabled=True, owner=owner,
            match={"text_contains": ["rechnung"]}, then={"document_type": "Rechnung"},
        )

    def test_eigene_regel_nur_auf_eigene_dokumente(self):
        self._rule(self.alice)
        alice_doc, bob_doc = self._doc(self.alice), self._doc(self.bob)
        apply_rules(alice_doc)
        apply_rules(bob_doc)
        alice_doc.refresh_from_db()
        bob_doc.refresh_from_db()
        self.assertIsNotNone(alice_doc.document_type)  # eigene Regel griff
        self.assertIsNone(bob_doc.document_type)        # fremde Regel griff NICHT

    def test_globale_regel_wirkt_auf_alle(self):
        self._rule(None)
        bob_doc = self._doc(self.bob)
        apply_rules(bob_doc)
        bob_doc.refresh_from_db()
        self.assertIsNotNone(bob_doc.document_type)
