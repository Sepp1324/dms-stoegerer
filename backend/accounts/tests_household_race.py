"""P1: Die „hoechstens ein Haushalt"-Invariante ist race-frei.

Frueher lief ``target.households.exists()`` VOR der Transaktion; zwei parallele
Freigaben (oder eine Freigabe + gleichzeitiges Anlegen eines Haushalts) konnten
beide den Check passieren und einen Nutzer zum Mitglied ZWEIER Haushalte machen.
Der Fix sperrt den Zielnutzer per ``select_for_update()`` INNERHALB der
Transaktion und prueft dort erneut; das Anlegen nimmt denselben Lock.
"""
import threading

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import Household, HouseholdJoinRequest, JoinRequestStatus

User = get_user_model()


class HouseholdInvariantSequentialTests(TestCase):
    """Deterministisch: Die verbindliche Pruefung sitzt IN der Transaktion, also
    wird eine zweite Freigabe abgelehnt, sobald der Nutzer bereits Mitglied ist."""

    def setUp(self):
        self.target = User.objects.create_user(
            username="target", password="pw", role="user"
        )
        self.owner_a = User.objects.create_user(
            username="owner_a", password="pw", role="user"
        )
        self.owner_b = User.objects.create_user(
            username="owner_b", password="pw", role="user"
        )
        self.house_a = Household.objects.create(
            name="A", owner=self.owner_a, created_by=self.owner_a
        )
        self.house_a.members.add(self.owner_a)
        self.house_b = Household.objects.create(
            name="B", owner=self.owner_b, created_by=self.owner_b
        )
        self.house_b.members.add(self.owner_b)
        self.req_a = HouseholdJoinRequest.objects.create(
            household=self.house_a, user=self.target, status=JoinRequestStatus.PENDING
        )
        self.req_b = HouseholdJoinRequest.objects.create(
            household=self.house_b, user=self.target, status=JoinRequestStatus.PENDING
        )

    def _decide(self, owner, house, req, decision="approve"):
        client = APIClient()
        client.force_authenticate(owner)
        return client.post(
            reverse("household-request-decide", args=[house.id, req.id]),
            {"decision": decision},
            format="json",
        )

    def test_zweite_freigabe_wird_abgelehnt(self):
        r1 = self._decide(self.owner_a, self.house_a, self.req_a)
        self.assertEqual(r1.status_code, 200, r1.data)

        r2 = self._decide(self.owner_b, self.house_b, self.req_b)
        self.assertEqual(r2.status_code, 400)

        # Der Nutzer ist in GENAU einem Haushalt (dem ersten).
        self.assertEqual(self.target.households.count(), 1)
        self.assertEqual(self.target.households.first().id, self.house_a.id)
        self.req_b.refresh_from_db()
        self.assertEqual(self.req_b.status, JoinRequestStatus.REJECTED)

    def test_freigabe_happy_path(self):
        r = self._decide(self.owner_a, self.house_a, self.req_a)
        self.assertEqual(r.status_code, 200, r.data)
        self.req_a.refresh_from_db()
        self.assertEqual(self.req_a.status, JoinRequestStatus.APPROVED)
        self.assertIn(self.target, self.house_a.members.all())


class HouseholdCreateInvariantTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="creator", password="pw", role="user"
        )

    def test_zweiter_haushalt_wird_abgelehnt(self):
        client = APIClient()
        client.force_authenticate(self.user)
        url = reverse("households")
        r1 = client.post(url, {"name": "Erster"}, format="json")
        self.assertEqual(r1.status_code, 201, r1.data)
        r2 = client.post(url, {"name": "Zweiter"}, format="json")
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(self.user.households.count(), 1)


class HouseholdParallelApprovalTests(TransactionTestCase):
    """Echte Nebenlaeufigkeit: zwei Freigaben desselben Nutzers gleichzeitig.

    ``select_for_update()`` wirkt nur auf einer Datenbank mit Row-Locking
    (PostgreSQL – so laeuft die Suite). Auf SQLite ist es ein No-op, daher wird
    der Test dort uebersprungen (er koennte die Serialisierung nicht zeigen).
    """

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("select_for_update braucht Row-Locking (PostgreSQL).")
        self.target = User.objects.create_user(
            username="race_target", password="pw", role="user"
        )
        self.owner_a = User.objects.create_user(
            username="race_owner_a", password="pw", role="user"
        )
        self.owner_b = User.objects.create_user(
            username="race_owner_b", password="pw", role="user"
        )
        self.house_a = Household.objects.create(
            name="A", owner=self.owner_a, created_by=self.owner_a
        )
        self.house_a.members.add(self.owner_a)
        self.house_b = Household.objects.create(
            name="B", owner=self.owner_b, created_by=self.owner_b
        )
        self.house_b.members.add(self.owner_b)
        self.req_a = HouseholdJoinRequest.objects.create(
            household=self.house_a, user=self.target, status=JoinRequestStatus.PENDING
        )
        self.req_b = HouseholdJoinRequest.objects.create(
            household=self.house_b, user=self.target, status=JoinRequestStatus.PENDING
        )

    def _approve(self, owner, house, req, results, idx, barrier):
        try:
            barrier.wait(timeout=5)
            client = APIClient()
            client.force_authenticate(owner)
            resp = client.post(
                reverse("household-request-decide", args=[house.id, req.id]),
                {"decision": "approve"},
                format="json",
            )
            results[idx] = resp.status_code
        finally:
            connections.close_all()

    def test_parallele_freigaben_ergeben_ein_mitglied(self):
        results = [None, None]
        barrier = threading.Barrier(2)
        threads = [
            threading.Thread(
                target=self._approve,
                args=(self.owner_a, self.house_a, self.req_a, results, 0, barrier),
            ),
            threading.Thread(
                target=self._approve,
                args=(self.owner_b, self.house_b, self.req_b, results, 1, barrier),
            ),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Genau eine Freigabe gewinnt (200), die andere prallt an der Invariante
        # ab (400) – nie zwei Mitgliedschaften.
        self.assertEqual(sorted(results), [200, 400], results)
        self.assertEqual(self.target.households.count(), 1)
