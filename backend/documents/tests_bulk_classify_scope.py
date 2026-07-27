"""P2: Asynchrone Bulk-Klassifizierung prüft die Rechte zum Ausführungszeitpunkt.

Ändert sich der Owner zwischen Request und Task-Lauf, darf der alte Auftrag kein
inzwischen fremdes Dokument klassifizieren – der Task re-scoped am Actor.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import tasks
from .models import Document

User = get_user_model()


class BulkClassifyScopeTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user("bc-actor", password="pw", role="user")
        self.other = User.objects.create_user("bc-other", password="pw", role="user")
        self.admin = User.objects.create_user("bc-admin", password="pw", role="admin")
        self.mine = Document.objects.create(title="mine", owner=self.actor)
        self.foreign = Document.objects.create(title="foreign", owner=self.other)

    def _run(self, ids, actor_id):
        captured = {}

        def fake_classify(docs):
            captured["ids"] = sorted(d.id for d in docs)
            return {"updated": 0, "unchanged": len(docs), "errors": []}

        with mock.patch(
            "documents.classification.classify_documents", side_effect=fake_classify
        ):
            tasks.bulk_classify_documents(ids, actor_id=actor_id)
        return captured["ids"]

    def test_nichtadmin_nur_eigene(self):
        ids = self._run([self.mine.id, self.foreign.id], self.actor.id)
        self.assertEqual(ids, [self.mine.id])  # fremdes Dokument ausgeschlossen

    def test_admin_alle(self):
        ids = self._run([self.mine.id, self.foreign.id], self.admin.id)
        self.assertEqual(ids, sorted([self.mine.id, self.foreign.id]))

    def test_ohne_actor_nichts(self):
        ids = self._run([self.mine.id, self.foreign.id], None)
        self.assertEqual(ids, [])  # fail-closed
