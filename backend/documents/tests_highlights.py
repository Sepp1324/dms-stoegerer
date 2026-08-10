"""Tests für positions-verankerte Markierungen/Notizen am Beleg (Studio Phase 2).

Kernzusagen: an eine Version gebunden; Seite/Geometrie gegen deren Layout validiert;
lesbar für alle, die den Beleg lesen dürfen (Owner + Haushalt); Anlegen/Löschen
strikt owner-/admin-only (Haushalts-Freigabe ist read-only); ``document``/``version``/
``created_by`` serverseitig gesetzt.
"""
import hashlib

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Household
from documents.models import (
    Document,
    DocumentHighlight,
    DocumentPageLayout,
    DocumentVersion,
)

User = get_user_model()


def _doc(owner, title, *, shared, page_count=3):
    doc = Document.objects.create(
        title=title, owner=owner, shared_with_household=shared
    )
    version = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path=f"/tmp/{title}.pdf",
        sha256=hashlib.sha256(title.encode()).hexdigest(),
        page_count=page_count,
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
        cls.version = cls.shared.current_version
        # Layout auf Seite 1 (für die Geometrie-Validierung).
        DocumentPageLayout.objects.create(
            version=cls.version, page_no=1, width=595.0, height=842.0,
            words=[{"t": "X", "bbox": [1, 2, 3, 4]}],
        )

    def _url(self, doc):
        return f"/api/documents/{doc.id}/highlights/"

    def _create(self, doc, **over):
        payload = {"page_no": 1, "bbox": [10, 20, 30, 40], "note": "Hallo", **over}
        return self.client.post(self._url(doc), payload, format="json")

    def test_owner_legt_an_versiongebunden_und_liest(self):
        self.client.force_authenticate(self.alice)
        resp = self._create(self.shared)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["created_by_username"], "alice")
        self.assertEqual(resp.data["document"], self.shared.id)
        self.assertEqual(resp.data["version"], self.version.id)
        self.assertEqual(resp.data["version_no"], 1)
        self.assertEqual(resp.data["bbox"], [10.0, 20.0, 30.0, 40.0])

        lst = self.client.get(self._url(self.shared))
        self.assertEqual(lst.status_code, 200)
        self.assertEqual(len(lst.data), 1)

    def test_liste_ist_nach_version_gefiltert(self):
        DocumentHighlight.objects.create(
            document=self.shared, version=self.version, page_no=1,
            bbox=[1, 2, 3, 4], created_by=self.alice,
        )
        # Eine zweite Version ohne Markierungen.
        v2 = DocumentVersion.objects.create(
            document=self.shared, version_no=2, file_path="/tmp/g2.pdf",
            sha256="a" * 64, page_count=3,
        )
        self.shared.current_version = v2
        self.shared.save(update_fields=["current_version"])
        self.client.force_authenticate(self.alice)
        # Ohne Param = aktuelle Version (v2) → keine Markierungen.
        self.assertEqual(len(self.client.get(self._url(self.shared)).data), 0)
        # ?version=1 → die Markierung von v1.
        self.assertEqual(
            len(self.client.get(self._url(self.shared) + "?version=1").data), 1
        )

    def test_haushaltsmitglied_liest_aber_schreibt_nicht(self):
        DocumentHighlight.objects.create(
            document=self.shared, version=self.version, page_no=1,
            bbox=[1, 2, 3, 4], created_by=self.alice,
        )
        self.client.force_authenticate(self.bob)
        # Lesen: erlaubt (Read-only-Freigabe).
        self.assertEqual(self.client.get(self._url(self.shared)).status_code, 200)
        # Anlegen: verboten (owner-only).
        self.assertEqual(self._create(self.shared).status_code, 403)

    def test_admin_darf_anlegen(self):
        self.client.force_authenticate(self.admin)
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

    def test_seite_ausserhalb_400(self):
        self.client.force_authenticate(self.alice)
        self.assertEqual(self._create(self.shared, page_no=99).status_code, 400)

    def test_box_ausserhalb_der_seite_400(self):
        self.client.force_authenticate(self.alice)
        # Seite 1 hat Layout 595x842 → Box weit darüber ist ungültig.
        self.assertEqual(
            self._create(self.shared, page_no=1, bbox=[10, 20, 900, 1200]).status_code,
            400,
        )

    def test_seite_ohne_layout_nur_seitenzahl_geprueft(self):
        # Seite 2 hat kein Layout → nur page_count zählt, Geometrie wird durchgelassen.
        self.client.force_authenticate(self.alice)
        self.assertEqual(
            self._create(self.shared, page_no=2, bbox=[0, 0, 9999, 9999]).status_code,
            201,
        )

    def test_loeschen_owner_only(self):
        hl = DocumentHighlight.objects.create(
            document=self.shared, version=self.version, page_no=1,
            bbox=[1, 2, 3, 4], created_by=self.alice,
        )
        url = f"/api/documents/{self.shared.id}/highlights/{hl.id}/"
        # Haushaltsmitglied (nicht Owner) erreicht die Löschung gar nicht (owner-only).
        self.client.force_authenticate(self.bob)
        self.assertEqual(self.client.delete(url).status_code, 404)
        # Owner darf.
        self.client.force_authenticate(self.alice)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(DocumentHighlight.objects.filter(pk=hl.id).exists())

    def test_admin_darf_fremde_markierung_loeschen(self):
        hl = DocumentHighlight.objects.create(
            document=self.shared, version=self.version, page_no=1,
            bbox=[1, 2, 3, 4], created_by=self.alice,
        )
        self.client.force_authenticate(self.admin)
        url = f"/api/documents/{self.shared.id}/highlights/{hl.id}/"
        self.assertEqual(self.client.delete(url).status_code, 204)

    def test_serverseitige_felder_nicht_uebernehmbar(self):
        other = _doc(self.bob, "bob-doc", shared=False)
        self.client.force_authenticate(self.alice)
        resp = self.client.post(
            self._url(self.shared),
            {
                "page_no": 1,
                "bbox": [10, 20, 30, 40],
                "document": other.id,
                "version": 999,
                "created_by": self.bob.id,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["document"], self.shared.id)
        self.assertEqual(resp.data["version"], self.version.id)
        self.assertEqual(resp.data["created_by_username"], "alice")
