"""P1/P2: Haushalt verlassen ist atomar + gesperrt und widerruft Freigaben."""
from django.test import TestCase
from rest_framework.test import APIClient

from documents.models import Document, DocumentFolder

from .models import Household, User


class HouseholdLeaveTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("h-owner", password="pw", role="user")
        self.member = User.objects.create_user("h-member", password="pw", role="user")
        self.hh = Household.objects.create(
            name="WG", created_by=self.owner, owner=self.owner, join_code="ABC123"
        )
        self.hh.members.add(self.owner, self.member)
        self.client = APIClient()

    def test_owner_austritt_uebergibt_ownership(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/households/{self.hh.id}/leave/")
        self.assertEqual(resp.status_code, 204)
        self.hh.refresh_from_db()
        self.assertEqual(self.hh.owner_id, self.member.id)
        self.assertNotIn(self.owner, self.hh.members.all())

    def test_austritt_widerruft_freigaben(self):
        # P1: shared_with_household darf nicht bestehen bleiben – sonst wären die
        # Dokumente/Ordner nach Beitritt zu einem ANDEREN Haushalt dort sichtbar.
        doc = Document.objects.create(
            title="Geteilt", owner=self.member, shared_with_household=True
        )
        folder = DocumentFolder.objects.create(
            name="Geteilter Ordner", owner=self.member, shared_with_household=True
        )
        # Fremde Freigabe (anderer Owner) darf NICHT angefasst werden.
        foreign = Document.objects.create(
            title="Owners geteiltes", owner=self.owner, shared_with_household=True
        )

        self.client.force_authenticate(self.member)
        resp = self.client.post(f"/api/households/{self.hh.id}/leave/")

        self.assertEqual(resp.status_code, 204)
        doc.refresh_from_db()
        folder.refresh_from_db()
        foreign.refresh_from_db()
        self.assertFalse(doc.shared_with_household)
        self.assertFalse(folder.shared_with_household)
        self.assertTrue(foreign.shared_with_household)  # fremd -> unberührt

    def test_letztes_mitglied_loescht_haushalt(self):
        # Erst der Nicht-Owner tritt aus, dann der Owner -> Haushalt weg.
        self.client.force_authenticate(self.member)
        self.assertEqual(
            self.client.post(f"/api/households/{self.hh.id}/leave/").status_code, 204
        )
        self.client.force_authenticate(self.owner)
        self.assertEqual(
            self.client.post(f"/api/households/{self.hh.id}/leave/").status_code, 204
        )
        self.assertFalse(Household.objects.filter(pk=self.hh.id).exists())
