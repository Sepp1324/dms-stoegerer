"""Tests für die Familien-/Haushalts-Freigabe – Fokus: Sicherheit.

Kernzusagen: Mitglieder dürfen freigegebene Fremd-Dokumente LESEN, aber niemals
SCHREIBEN; Nicht-Mitglieder sehen nichts; private Dokumente bleiben privat.
"""
import hashlib

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Household
from documents.models import Document, DocumentVersion

User = get_user_model()


def _doc(owner, title, *, shared):
    doc = Document.objects.create(
        title=title, owner=owner, shared_with_household=shared
    )
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


class HouseholdSharingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("alice", password="pw", role="user")
        cls.bob = User.objects.create_user("bob", password="pw", role="user")
        cls.carol = User.objects.create_user("carol", password="pw", role="user")
        household = Household.objects.create(name="Familie", created_by=cls.alice)
        household.members.add(cls.alice, cls.bob)  # carol bewusst NICHT drin
        cls.shared = _doc(cls.alice, "Alice geteilt", shared=True)
        cls.private = _doc(cls.alice, "Alice privat", shared=False)

    def test_member_can_read_shared_with_owner_name(self):
        self.client.force_authenticate(self.bob)
        resp = self.client.get(f"/api/documents/{self.shared.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["owner_username"], "alice")

    def test_member_cannot_read_private(self):
        self.client.force_authenticate(self.bob)
        self.assertEqual(
            self.client.get(f"/api/documents/{self.private.id}/").status_code, 404
        )

    def test_nonmember_cannot_read_shared(self):
        self.client.force_authenticate(self.carol)
        self.assertEqual(
            self.client.get(f"/api/documents/{self.shared.id}/").status_code, 404
        )

    def test_shared_in_member_list_private_not(self):
        self.client.force_authenticate(self.bob)
        resp = self.client.get("/api/documents/")
        ids = [d["id"] for d in resp.data["results"]]
        self.assertIn(self.shared.id, ids)
        self.assertNotIn(self.private.id, ids)

    def test_member_cannot_write_shared(self):
        self.client.force_authenticate(self.bob)
        # PATCH → owner-only queryset → 404 (kein Leak)
        self.assertEqual(
            self.client.patch(
                f"/api/documents/{self.shared.id}/", {"title": "Gekapert"}, format="json"
            ).status_code,
            404,
        )
        # mutierende Sub-Action ebenfalls 404
        self.assertEqual(
            self.client.post(
                f"/api/documents/{self.shared.id}/supersede/",
                {"by": self.private.id},
                format="json",
            ).status_code,
            404,
        )
        self.shared.refresh_from_db()
        self.assertEqual(self.shared.title, "Alice geteilt")

    def test_owner_can_write(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.patch(
            f"/api/documents/{self.shared.id}/", {"title": "Neu"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)

    def test_share_toggle_requires_household(self):
        loner = User.objects.create_user("loner", password="pw", role="user")
        doc = _doc(loner, "Loner", shared=False)
        self.client.force_authenticate(loner)
        resp = self.client.post(
            f"/api/documents/{doc.id}/share-household/", {"shared": True}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_owner_toggles_share(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.post(
            f"/api/documents/{self.private.id}/share-household/",
            {"shared": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.private.refresh_from_db()
        self.assertTrue(self.private.shared_with_household)


class HouseholdApiTests(APITestCase):
    def test_create_add_member_leave(self):
        a = User.objects.create_user("ha", password="pw", role="user")
        User.objects.create_user("hb", password="pw", role="user")
        self.client.force_authenticate(a)

        created = self.client.post("/api/households/", {"name": "Zuhause"}, format="json")
        self.assertEqual(created.status_code, 201)
        hid = created.data["id"]

        mine = self.client.get("/api/households/")
        self.assertEqual(mine.data["name"], "Zuhause")

        added = self.client.post(
            f"/api/households/{hid}/members/", {"username": "hb"}, format="json"
        )
        self.assertEqual(added.status_code, 200)
        self.assertEqual(len(added.data["members"]), 2)

        left = self.client.post(f"/api/households/{hid}/leave/")
        self.assertEqual(left.status_code, 204)

    def test_cannot_create_second_household(self):
        a = User.objects.create_user("h2a", password="pw", role="user")
        self.client.force_authenticate(a)
        self.client.post("/api/households/", {"name": "A"}, format="json")
        second = self.client.post("/api/households/", {"name": "B"}, format="json")
        self.assertEqual(second.status_code, 400)

    def test_add_member_requires_membership(self):
        a = User.objects.create_user("h3a", password="pw", role="user")
        b = User.objects.create_user("h3b", password="pw", role="user")
        self.client.force_authenticate(a)
        hid = self.client.post("/api/households/", {"name": "A"}, format="json").data["id"]
        # b ist nicht Mitglied → darf niemanden hinzufügen
        self.client.force_authenticate(b)
        resp = self.client.post(
            f"/api/households/{hid}/members/", {"username": "h3a"}, format="json"
        )
        self.assertEqual(resp.status_code, 404)
