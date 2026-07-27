"""P1: Inbox-Kandidaten werden atomar übernommen (kein Doppel-Apply).

Zwei parallele Apply-Requests desselben „Neue Akte"-Kandidaten dürfen NICHT beide
erfolgreich sein – sonst entstünden zwei Akten (eine verwaist). Der Row-Lock in
``apply_case_candidate`` serialisiert die Requests.

Benötigt echte Zeilensperren -> nur unter PostgreSQL aussagekräftig.
"""
import threading

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from .models import CaseFile, CaseFileCandidate, Document

User = get_user_model()


class CaseCandidateConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Nebenläufigkeit nur unter PostgreSQL testbar")
        self.user = User.objects.create_user("cand", password="pw", role="user")
        self.doc = Document.objects.create(title="Beleg", owner=self.user)
        self.candidate = CaseFileCandidate.objects.create(
            document=self.doc,
            kind=CaseFileCandidate.Kind.NEW_CASE,
            suggested_title="Neue Akte",
            status=CaseFileCandidate.Status.PENDING,
        )

    def test_paralleles_apply_erzeugt_nur_eine_akte(self):
        url = (
            f"/api/documents/{self.doc.id}/case-candidates/"
            f"{self.candidate.id}/apply/"
        )
        barrier = threading.Barrier(2, timeout=10)
        results: list[int] = []
        lock = threading.Lock()

        def call():
            client = APIClient()
            client.force_authenticate(self.user)
            try:
                barrier.wait()
                resp = client.post(url)
                with lock:
                    results.append(resp.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=call) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)

        self.assertFalse(any(t.is_alive() for t in threads), "Thread hängt (Deadlock?)")
        self.assertEqual(sorted(results), [200, 409], results)
        # Genau EINE Akte – kein verwaistes Duplikat.
        self.assertEqual(CaseFile.objects.filter(owner=self.user).count(), 1)
