"""Tests für die „Geteilt"-Ansicht (?shared=with-me | by-me)."""
import hashlib

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Household
from documents.models import Document, DocumentVersion

User = get_user_model()


def _doc(owner, title, *, shared=False):
    doc = Document.objects.create(title=title, owner=owner, shared_with_household=shared)
    version = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path=f"/tmp/{title}.pdf",
        sha256=hashlib.sha256(title.encode()).hexdigest(),
        ocr_text="Inhalt",
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    return doc


class SharedViewTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("s_alice", password="pw", role="user")
        cls.bob = User.objects.create_user("s_bob", password="pw", role="user")
        household = Household.objects.create(name="Fam", created_by=cls.alice)
        household.members.add(cls.alice, cls.bob)
        cls.alice_shared = _doc(cls.alice, "AliceShared", shared=True)
        cls.alice_private = _doc(cls.alice, "AlicePrivate", shared=False)
        cls.bob_own = _doc(cls.bob, "BobOwn", shared=False)

    def _ids(self, params):
        return [d["id"] for d in self.client.get(f"/api/documents/{params}").data["results"]]

    def test_with_me_shows_only_shared_foreign(self):
        self.client.force_authenticate(self.bob)
        ids = self._ids("?shared=with-me")
        self.assertIn(self.alice_shared.id, ids)
        self.assertNotIn(self.bob_own.id, ids)      # eigenes ausgeblendet
        self.assertNotIn(self.alice_private.id, ids)  # nicht geteilt

    def test_by_me_shows_own_shared_only(self):
        self.client.force_authenticate(self.alice)
        ids = self._ids("?shared=by-me")
        self.assertIn(self.alice_shared.id, ids)
        self.assertNotIn(self.alice_private.id, ids)

    def test_no_filter_shows_own_plus_shared(self):
        self.client.force_authenticate(self.bob)
        ids = self._ids("")
        self.assertIn(self.bob_own.id, ids)
        self.assertIn(self.alice_shared.id, ids)
