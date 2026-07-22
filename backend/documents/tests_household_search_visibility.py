"""P2: Haushalts-Freigaben müssen AUCH im Copilot (AskView) und in der
semantischen Suche (SemanticSearchView) sichtbar sein – nicht nur owner-gescoped.
Beide Oberflächen führen ihren Dokument-Zugriff jetzt über _visible_documents_for().
"""
import hashlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Household
from documents.models import Document, DocumentVersion

User = get_user_model()


def _doc(owner, title, *, shared):
    doc = Document.objects.create(title=title, owner=owner, shared_with_household=shared)
    version = DocumentVersion.objects.create(
        document=doc, version_no=1, file_path=f"/tmp/{title}.pdf",
        sha256=hashlib.sha256(title.encode()).hexdigest(), ocr_text="Inhalt",
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    return doc


class HouseholdSearchVisibilityTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("alice", password="pw", role="user")
        cls.bob = User.objects.create_user("bob", password="pw", role="user")
        cls.carol = User.objects.create_user("carol", password="pw", role="user")  # kein Mitglied
        hh = Household.objects.create(name="Familie", created_by=cls.alice)
        hh.members.add(cls.alice, cls.bob)
        cls.shared = _doc(cls.alice, "Alice geteilt", shared=True)
        cls.private = _doc(cls.alice, "Alice privat", shared=False)

    def _ask_qs_ids(self, user):
        """Ruft den Copilot auf und gibt die IDs der berücksichtigten Dokumente zurück."""
        self.client.force_authenticate(user)
        with patch("ai.services.answer_question", return_value={"source": "test", "sources": []}) as m:
            resp = self.client.post("/api/ask/", {"question": "Worum geht es?"}, format="json")
        self.assertEqual(resp.status_code, 200)
        qs = m.call_args.args[1]
        return {d.id for d in qs}

    def _semantic_qs_ids(self, user):
        self.client.force_authenticate(user)
        with patch(
            "documents.views.semantic_index_service.search_documents", return_value=[]
        ) as m:
            resp = self.client.post("/api/search/semantic/", {"q": "Vertrag"}, format="json")
        self.assertEqual(resp.status_code, 200)
        qs = m.call_args.args[1]
        return {d.id for d in qs}

    # --- Copilot (AskView) ---
    def test_copilot_member_sieht_geteiltes_nicht_privates(self):
        ids = self._ask_qs_ids(self.bob)
        self.assertIn(self.shared.id, ids)
        self.assertNotIn(self.private.id, ids)

    def test_copilot_nichtmitglied_sieht_nichts(self):
        ids = self._ask_qs_ids(self.carol)
        self.assertNotIn(self.shared.id, ids)
        self.assertNotIn(self.private.id, ids)

    # --- Semantische Suche ---
    def test_semantik_member_sieht_geteiltes_nicht_privates(self):
        ids = self._semantic_qs_ids(self.bob)
        self.assertIn(self.shared.id, ids)
        self.assertNotIn(self.private.id, ids)

    def test_semantik_nichtmitglied_sieht_nichts(self):
        ids = self._semantic_qs_ids(self.carol)
        self.assertNotIn(self.shared.id, ids)
        self.assertNotIn(self.private.id, ids)

    def test_owner_sieht_eigene_in_beiden(self):
        self.assertEqual({self.shared.id, self.private.id}, self._ask_qs_ids(self.alice))
        self.assertEqual({self.shared.id, self.private.id}, self._semantic_qs_ids(self.alice))
