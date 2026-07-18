"""Tests: Batch-Endpoint für Smart-Inbox-Kandidaten (#1, Request-Storm).

Ein Request liefert Extraction- + Akten-Kandidaten für mehrere Dokumente
(owner-gescoped) mit konstanter Query-Zahl (kein N+1 → kein 2·N-Storm).
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from .models import CaseFileCandidate, Document, ExtractionCandidate


class InboxCandidatesBatchTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("inbox_owner", password="pw", role="user")
        self.other = User.objects.create_user("inbox_other", password="pw", role="user")

    def _doc(self, owner, title="Doc"):
        return Document.objects.create(owner=owner, title=title)

    def _extraction(self, doc):
        return ExtractionCandidate.objects.create(
            document=doc,
            field=ExtractionCandidate.Field.DOCUMENT_DATE,
            value="01.01.2026",
            normalized_value="2026-01-01",
            confidence=70,
        )

    def _case(self, doc):
        return CaseFileCandidate.objects.create(
            document=doc,
            kind=CaseFileCandidate.Kind.NEW_CASE,
            suggested_title="Neue Akte",
            signature=f"new:{doc.id}",
            score=55,
        )

    def _url(self, ids):
        return "/api/documents/inbox-candidates/?ids=" + ",".join(str(i) for i in ids)

    def test_batch_returns_candidates_and_is_owner_scoped(self):
        d1 = self._doc(self.owner)
        d2 = self._doc(self.owner)  # ohne Kandidaten
        foreign = self._doc(self.other)
        self._extraction(d1)
        self._case(d1)

        self.client.force_authenticate(self.owner)
        resp = self.client.get(self._url([d1.id, d2.id, foreign.id]))

        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertEqual(len(data[str(d1.id)]["extraction"]), 1)
        self.assertEqual(len(data[str(d1.id)]["cases"]), 1)
        self.assertEqual(data[str(d2.id)]["extraction"], [])
        self.assertEqual(data[str(d2.id)]["cases"], [])
        # Fremdes Dokument taucht NICHT auf (Owner-Isolation).
        self.assertNotIn(str(foreign.id), data)

    def test_query_count_is_constant(self):
        small = [self._doc(self.owner) for _ in range(2)]
        big = [self._doc(self.owner) for _ in range(6)]
        for doc in small + big:
            self._extraction(doc)
            self._case(doc)
        self.client.force_authenticate(self.owner)

        # Aufwärm-Request (einmalige Caches wie ContentTypes nicht mitmessen).
        self.client.get(self._url([small[0].id]))

        def count(ids):
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(self._url(ids))
            return len(ctx)

        # 2 vs. 6 Dokumente → gleiche Query-Zahl (Prefetch statt 2·N Requests).
        self.assertEqual(
            count([d.id for d in small]), count([d.id for d in big])
        )
