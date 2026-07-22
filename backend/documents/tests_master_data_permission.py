"""P2: Globale Stammdaten (Tags/Korrespondenten/Typen/Ablagepfade/Zusatzfelder)
– Anlegen für Writer, aber Umbenennen/Löschen nur für Admins (sonst kann ein
Nutzer Metadaten aller Nutzer global verändern). Tag steht repräsentativ für die
gemeinsame Permission ReadCreateOrAdminMutate."""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from documents.models import Correspondent, Tag

User = get_user_model()


class MasterDataPermissionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.writer = User.objects.create_user("md_writer", password="pw", role="user")
        cls.admin = User.objects.create_user("md_admin", password="pw", role="admin")
        cls.tag = Tag.objects.create(name="Finanzen")

    def test_writer_darf_anlegen(self):
        self.client.force_authenticate(self.writer)
        resp = self.client.post("/api/tags/", {"name": "Neu"}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_writer_darf_nicht_umbenennen(self):
        self.client.force_authenticate(self.writer)
        resp = self.client.patch(f"/api/tags/{self.tag.id}/", {"name": "Gekapert"}, format="json")
        self.assertEqual(resp.status_code, 403)
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.name, "Finanzen")

    def test_writer_darf_nicht_loeschen(self):
        self.client.force_authenticate(self.writer)
        resp = self.client.delete(f"/api/tags/{self.tag.id}/")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Tag.objects.filter(id=self.tag.id).exists())

    def test_admin_darf_umbenennen_und_loeschen(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(
            self.client.patch(
                f"/api/tags/{self.tag.id}/", {"name": "Umbenannt"}, format="json"
            ).status_code,
            200,
        )
        self.assertEqual(self.client.delete(f"/api/tags/{self.tag.id}/").status_code, 204)

    def test_korrespondent_gleiche_regel(self):
        corr = Correspondent.objects.create(name="Stadtwerke")
        self.client.force_authenticate(self.writer)
        self.assertEqual(
            self.client.patch(
                f"/api/correspondents/{corr.id}/", {"name": "X"}, format="json"
            ).status_code,
            403,
        )
        self.client.force_authenticate(self.admin)
        self.assertEqual(
            self.client.patch(
                f"/api/correspondents/{corr.id}/", {"name": "X"}, format="json"
            ).status_code,
            200,
        )
