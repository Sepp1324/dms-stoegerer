"""Tests für positions-verankerte Markierungen/Notizen am Beleg (Studio Phase 2).

Kernzusagen: sichtbar für alle, die den Beleg lesen dürfen (Owner + Haushalt);
Anlegen nur mit Schreibrecht; Löschen nur durch den Ersteller (oder Admin);
``document``/``created_by`` serverseitig gesetzt; bbox wird validiert.
"""
import hashlib

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Household
from documents.models import Document, DocumentHighlight, DocumentVersion

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
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    return doc


class HighlightTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("alice", password="pw", role="user")
        cls.bob = User.objects.create_user("bob", password="pw", role="user")
        cls.carol = User.objects.create_user("carol", password="pw", role="user")
        cls.guest = User.objects.create_user("guest", password="pw", role="guest")
        cls.admin = User.objects.create_user("admin", password="pw", role="admin")
        hh = Household.objects.create(name="Familie", created_by=cls.alice)
        hh.members.add(cls.alice, cls.bob, cls.guest, cls.admin)  # carol NICHT drin
        cls.shared = _doc(cls.alice, "geteilt", shared=True)
        cls.private = _doc(cls.alice, "privat", shared=False)

    def _url(self, doc):
        return f"/api/documents/{doc.id}/highlights/"

    def _create(self, doc, **over):
        payload = {"page_no": 1, "bbox": [10, 20, 30, 40], "note": "Hallo", **over}
        return self.client.post(self._url(doc), payload, format="json")

    def test_owner_legt_an_und_liest(self):
        self.client.force_authenticate(self.alice)
        resp = self._create(self.shared)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["created_by_username"], "alice")
        self.assertEqual(resp.data["document"], self.shared.id)
        self.assertEqual(resp.data["bbox"], [10.0, 20.0, 30.0, 40.0])

        lst = self.client.get(self._url(self.shared))
        self.assertEqual(lst.status_code, 200)
        self.assertEqual(len(lst.data), 1)

    def test_haushaltsmitglied_sieht_und_ergaenzt_geteilten_beleg(self):
        DocumentHighlight.objects.create(
            document=self.shared, page_no=1, bbox=[1, 2, 3, 4], created_by=self.alice
        )
        self.client.force_authenticate(self.bob)
        lst = self.client.get(self._url(self.shared))
        self.assertEqual(lst.status_code, 200)
        self.assertEqual(len(lst.data), 1)
        # Mitglied darf eine eigene Markierung ergänzen.
        self.assertEqual(self._create(self.shared).status_code, 201)

    def test_nichtmitglied_sieht_nichts(self):
        self.client.force_authenticate(self.carol)
        self.assertEqual(self.client.get(self._url(self.shared)).status_code, 404)
        self.assertEqual(self._create(self.shared).status_code, 404)

    def test_gast_darf_nicht_anlegen(self):
        self.client.force_authenticate(self.guest)
        self.assertEqual(self._create(self.shared).status_code, 403)

    def test_ungueltige_bbox_400(self):
        self.client.force_authenticate(self.alice)
        self.assertEqual(self._create(self.shared, bbox=[1, 2, 3]).status_code, 400)
        self.assertEqual(self._create(self.shared, bbox="x").status_code, 400)

    def test_loeschen_nur_durch_ersteller(self):
        hl = DocumentHighlight.objects.create(
            document=self.shared, page_no=1, bbox=[1, 2, 3, 4], created_by=self.alice
        )
        url = f"/api/documents/{self.shared.id}/highlights/{hl.id}/"
        # Anderes Mitglied darf NICHT löschen.
        self.client.force_authenticate(self.bob)
        self.assertEqual(self.client.delete(url).status_code, 403)
        # Ersteller darf.
        self.client.force_authenticate(self.alice)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(DocumentHighlight.objects.filter(pk=hl.id).exists())

    def test_mitglied_darf_eigene_markierung_auf_geteiltem_beleg_loeschen(self):
        hl = DocumentHighlight.objects.create(
            document=self.shared, page_no=1, bbox=[1, 2, 3, 4], created_by=self.bob
        )
        self.client.force_authenticate(self.bob)
        url = f"/api/documents/{self.shared.id}/highlights/{hl.id}/"
        self.assertEqual(self.client.delete(url).status_code, 204)

    def test_admin_darf_fremde_markierung_loeschen(self):
        hl = DocumentHighlight.objects.create(
            document=self.shared, page_no=1, bbox=[1, 2, 3, 4], created_by=self.bob
        )
        self.client.force_authenticate(self.admin)
        url = f"/api/documents/{self.shared.id}/highlights/{hl.id}/"
        self.assertEqual(self.client.delete(url).status_code, 204)

    def test_document_und_ersteller_nicht_aus_request_uebernehmbar(self):
        # Auch wenn der Client document/created_by mitschickt, gewinnt der Server.
        self.client.force_authenticate(self.bob)
        resp = self.client.post(
            self._url(self.shared),
            {
                "page_no": 2,
                "bbox": [0, 0, 1, 1],
                "document": self.private.id,
                "created_by": self.alice.id,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["document"], self.shared.id)
        self.assertEqual(resp.data["created_by_username"], "bob")
