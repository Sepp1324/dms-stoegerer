"""Tests für die ordnerweite Familien-Freigabe (inkl. Vererbung auf Unterordner)."""
import hashlib

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Household
from documents.models import Document, DocumentFolder, DocumentVersion

User = get_user_model()


def _doc(owner, title, *, folder=None):
    doc = Document.objects.create(title=title, owner=owner, folder=folder)
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


class FolderSharingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("f_alice", password="pw", role="user")
        cls.bob = User.objects.create_user("f_bob", password="pw", role="user")
        cls.carol = User.objects.create_user("f_carol", password="pw", role="user")
        household = Household.objects.create(name="Fam", created_by=cls.alice)
        household.members.add(cls.alice, cls.bob)  # carol NICHT

        cls.shared_folder = DocumentFolder.objects.create(
            name="Familie", shared_with_household=True
        )
        cls.sub_folder = DocumentFolder.objects.create(
            name="Unterordner", parent=cls.shared_folder
        )  # erbt Freigabe
        cls.private_folder = DocumentFolder.objects.create(name="Privat")

        cls.in_shared = _doc(cls.alice, "InShared", folder=cls.shared_folder)
        cls.in_sub = _doc(cls.alice, "InSub", folder=cls.sub_folder)
        cls.in_private = _doc(cls.alice, "InPrivate", folder=cls.private_folder)
        cls.no_folder = _doc(cls.alice, "NoFolder", folder=None)

    def _list_ids(self):
        return [d["id"] for d in self.client.get("/api/documents/").data["results"]]

    def test_member_sees_shared_folder_and_subfolder(self):
        self.client.force_authenticate(self.bob)
        ids = self._list_ids()
        self.assertIn(self.in_shared.id, ids)
        self.assertIn(self.in_sub.id, ids)  # Vererbung auf Unterordner
        self.assertNotIn(self.in_private.id, ids)
        self.assertNotIn(self.no_folder.id, ids)

    def test_member_reads_but_cannot_write(self):
        self.client.force_authenticate(self.bob)
        self.assertEqual(
            self.client.get(f"/api/documents/{self.in_sub.id}/").status_code, 200
        )
        self.assertEqual(
            self.client.patch(
                f"/api/documents/{self.in_sub.id}/", {"title": "x"}, format="json"
            ).status_code,
            404,
        )

    def test_nonmember_sees_nothing_shared(self):
        self.client.force_authenticate(self.carol)
        ids = self._list_ids()
        self.assertNotIn(self.in_shared.id, ids)
        self.assertNotIn(self.in_sub.id, ids)

    def test_toggle_folder_share_reveals_docs(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.patch(
            f"/api/folders/{self.private_folder.id}/",
            {"shared_with_household": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.private_folder.refresh_from_db()
        self.assertTrue(self.private_folder.shared_with_household)

        self.client.force_authenticate(self.bob)
        self.assertIn(self.in_private.id, self._list_ids())
