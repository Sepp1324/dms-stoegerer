"""P1: Freigabe-Übergänge sind gegen parallele Entscheidungen geschützt.

Gleichzeitiges „Genehmigen" und „Ablehnen" desselben Dokuments darf NICHT zwei
Erfolgs-Audits aus demselben Ausgangsstatus erzeugen. Die Zeilensperre in
``_transition`` serialisiert die Übergänge: genau einer gewinnt, der andere 409.

Benötigt echte Zeilensperren -> nur unter PostgreSQL aussagekräftig.
"""
import threading

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from .models import AuditLogEntry, Document

User = get_user_model()


class TransitionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Nebenläufigkeit nur unter PostgreSQL testbar")
        self.user = User.objects.create_user("tr", password="pw", role="user")
        self.doc = Document.objects.create(
            title="Freigabe",
            owner=self.user,
            status=Document.ApprovalStatus.ZUR_FREIGABE,
        )

    def test_parallel_approve_reject_nur_einer_gewinnt(self):
        barrier = threading.Barrier(2, timeout=10)
        results: dict[str, int] = {}

        def call(name, path):
            client = APIClient()
            client.force_authenticate(self.user)
            try:
                barrier.wait()
                resp = client.post(f"/api/documents/{self.doc.id}/{path}/")
                results[name] = resp.status_code
            finally:
                connection.close()

        threads = [
            threading.Thread(target=call, args=("approve", "approve")),
            threading.Thread(target=call, args=("reject", "reject")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)

        self.assertFalse(any(t.is_alive() for t in threads), "Thread hängt (Deadlock?)")
        # Genau einer 200, einer 409 – nie beide erfolgreich.
        self.assertEqual(sorted(results.values()), [200, 409], results)
        # Und genau EIN Übergangs-Audit (approve XOR reject).
        transitions = AuditLogEntry.objects.filter(
            object_id=str(self.doc.id), action__in=["approve", "reject"]
        )
        self.assertEqual(transitions.count(), 1)
        # Endstatus passt zum Gewinner.
        self.doc.refresh_from_db()
        winner = transitions.first().action
        expected = (
            Document.ApprovalStatus.FREIGEGEBEN
            if winner == "approve"
            else Document.ApprovalStatus.ABGELEHNT
        )
        self.assertEqual(self.doc.status, expected)
