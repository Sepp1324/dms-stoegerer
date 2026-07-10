from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import Document, DocumentFolder, SavedView, Tag

User = get_user_model()


class SavedViewApiTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="saved_owner", password="pw", role="user"
        )
        self.other = User.objects.create_user(
            username="saved_other", password="pw", role="user"
        )
        self.guest = User.objects.create_user(
            username="saved_guest", password="pw", role="guest"
        )
        self.folder = DocumentFolder.objects.create(name="Finanzen")
        self.tag = Tag.objects.create(name="Steuer", color="#22c55e")

        self.matching_doc = Document.objects.create(
            title="Wien Energie Rechnung",
            owner=self.owner,
            folder=self.folder,
        )
        self.matching_doc.tags.add(self.tag)
        Document.objects.create(title="Privat ohne Ordner", owner=self.owner)
        Document.objects.create(
            title="Fremde Wien Energie Rechnung",
            owner=self.other,
            folder=self.folder,
        )

    def test_listet_nur_eigene_ansichten_mit_owner_sicherem_count(self):
        SavedView.objects.create(
            owner=self.owner,
            name="Meine Energie",
            query={"q": "Wien", "folder": self.folder.id},
        )
        SavedView.objects.create(
            owner=self.other,
            name="Fremde Ansicht",
            query={"folder": self.folder.id},
        )

        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/saved-views/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        item = response.data["results"][0]
        self.assertEqual(item["name"], "Meine Energie")
        # Das fremde Dokument im gleichen Ordner darf nicht in den Count laufen.
        self.assertEqual(item["count"], 1)

    def test_create_normalisiert_query_und_ignoriert_triage_owner(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/saved-views/",
            {
                "name": "  Ohne Ordner  ",
                "query": {
                    "folder": "none",
                    "owner": "none",
                    "page": 4,
                    "customFilters": {
                        f"custom_field_{self.tag.id}_gte": "10",
                        "ignored": "x",
                    },
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Ohne Ordner")
        self.assertEqual(
            response.data["query"],
            {
                "folder": "none",
                "customFilters": {f"custom_field_{self.tag.id}_gte": "10"},
            },
        )
        self.assertEqual(SavedView.objects.get().owner, self.owner)

    def test_default_ansicht_ist_pro_nutzer_eindeutig(self):
        first = SavedView.objects.create(
            owner=self.owner,
            name="Alt",
            query={"folder": self.folder.id},
            is_default=True,
        )
        second = SavedView.objects.create(
            owner=self.owner,
            name="Neu",
            query={"q": "Wien"},
        )

        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"/api/saved-views/{second.id}/",
            {"is_default": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    def test_gast_darf_gespeicherte_ansichten_lesen_aber_nicht_anlegen(self):
        SavedView.objects.create(owner=self.guest, name="Lesbar", query={})
        self.client.force_authenticate(self.guest)

        list_response = self.client.get("/api/saved-views/")
        create_response = self.client.post(
            "/api/saved-views/",
            {"name": "Neu", "query": {}},
            format="json",
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(create_response.status_code, 403)
