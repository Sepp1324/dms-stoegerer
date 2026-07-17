"""Tests für den Copilot-Agent.

Der Execute-Pfad ist sicherheitskritisch und deterministisch → voll getestet.
Der Plan-Pfad hängt am LLM → Provider und Kandidatensuche werden gemockt, geprüft
wird die Parsing-/Validierungslogik.
"""
import hashlib
import json
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from documents.models import (
    Correspondent,
    Document,
    DocumentFolder,
    DocumentReminder,
    DocumentType,
    DocumentVersion,
    Tag,
)
from documents.services import agent

User = get_user_model()


def _doc(owner, title="Vertrag"):
    doc = Document.objects.create(title=title, owner=owner)
    version = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path=f"/tmp/{title}.pdf",
        sha256=hashlib.sha256(f"{title}{owner.id}".encode()).hexdigest(),
        ocr_text="Inhalt",
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    return doc


class AgentExecuteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("agent-u", password="pw", role="user")
        cls.other = User.objects.create_user("agent-o", password="pw", role="user")
        cls.doc = _doc(cls.user)
        cls.foreign = _doc(cls.other, "Fremd")

    def test_add_tag(self):
        res = agent.execute(
            self.user, [{"action": "add_tag", "document": self.doc.id, "params": {"tag": "Steuer"}}]
        )
        self.assertEqual(len(res["applied"]), 1)
        self.assertEqual(res["errors"], [])
        self.assertTrue(self.doc.tags.filter(name="Steuer").exists())

    def test_set_note(self):
        agent.execute(
            self.user,
            [{"action": "set_note", "document": self.doc.id, "params": {"note": "Wichtig"}}],
        )
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.note, "Wichtig")

    def test_set_reminder(self):
        due = (date.today() + timedelta(days=30)).isoformat()
        res = agent.execute(
            self.user,
            [{"action": "set_reminder", "document": self.doc.id, "params": {"date": due, "note": "Kündigen"}}],
        )
        self.assertEqual(len(res["applied"]), 1)
        self.assertTrue(DocumentReminder.objects.filter(document=self.doc, note="Kündigen").exists())

    def test_reminder_bad_date_errors(self):
        res = agent.execute(
            self.user,
            [{"action": "set_reminder", "document": self.doc.id, "params": {"date": "morgen"}}],
        )
        self.assertEqual(res["applied"], [])
        self.assertEqual(len(res["errors"]), 1)

    def test_foreign_document_rejected(self):
        res = agent.execute(
            self.user, [{"action": "add_tag", "document": self.foreign.id, "params": {"tag": "X"}}]
        )
        self.assertEqual(res["applied"], [])
        self.assertEqual(len(res["errors"]), 1)
        self.assertFalse(self.foreign.tags.exists())

    def test_unknown_action_rejected(self):
        res = agent.execute(
            self.user, [{"action": "delete_everything", "document": self.doc.id, "params": {}}]
        )
        self.assertEqual(res["applied"], [])
        self.assertEqual(len(res["errors"]), 1)

    def test_set_correspondent_and_type(self):
        agent.execute(
            self.user,
            [
                {"action": "set_correspondent", "document": self.doc.id, "params": {"name": "Finanzamt"}},
                {"action": "set_document_type", "document": self.doc.id, "params": {"name": "Bescheid"}},
            ],
        )
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.correspondent, Correspondent.objects.get(name="Finanzamt"))
        self.assertEqual(self.doc.document_type, DocumentType.objects.get(name="Bescheid"))

    def test_move_to_existing_folder(self):
        folder = DocumentFolder.objects.create(name="Steuern")
        res = agent.execute(
            self.user,
            [{"action": "move_to_folder", "document": self.doc.id, "params": {"folder": "Steuern"}}],
        )
        self.assertEqual(len(res["applied"]), 1)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.folder_id, folder.id)

    def test_move_to_unknown_folder_errors(self):
        res = agent.execute(
            self.user,
            [{"action": "move_to_folder", "document": self.doc.id, "params": {"folder": "GibtsNicht"}}],
        )
        self.assertEqual(res["applied"], [])
        self.assertEqual(len(res["errors"]), 1)
        self.doc.refresh_from_db()
        self.assertIsNone(self.doc.folder_id)


class AgentUndoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("undo-u", password="pw", role="user")
        cls.other = User.objects.create_user("undo-o", password="pw", role="user")

    def _apply(self, doc, action, params):
        res = agent.execute(self.user, [{"action": action, "document": doc.id, "params": params}])
        self.assertEqual(len(res["applied"]), 1, res)
        return res["applied"][0]["audit_id"]

    def test_undo_add_tag_removes_it(self):
        doc = _doc(self.user, "UndoTag")
        audit_id = self._apply(doc, "add_tag", {"tag": "Temporär"})
        self.assertTrue(doc.tags.filter(name="Temporär").exists())

        res = agent.undo(self.user, audit_id)

        self.assertEqual(res["status"], "ok")
        self.assertFalse(doc.tags.filter(name="Temporär").exists())

    def test_undo_set_note_restores_previous(self):
        doc = _doc(self.user, "UndoNote")
        doc.note = "Alt"
        doc.save(update_fields=["note"])
        audit_id = self._apply(doc, "set_note", {"note": "Neu"})

        agent.undo(self.user, audit_id)

        doc.refresh_from_db()
        self.assertEqual(doc.note, "Alt")

    def test_undo_reminder_deletes_it(self):
        doc = _doc(self.user, "UndoRem")
        due = (date.today() + timedelta(days=10)).isoformat()
        audit_id = self._apply(doc, "set_reminder", {"date": due, "note": "X"})
        self.assertTrue(DocumentReminder.objects.filter(document=doc).exists())

        agent.undo(self.user, audit_id)

        self.assertFalse(DocumentReminder.objects.filter(document=doc).exists())

    def test_undo_move_to_folder_restores_previous(self):
        doc = _doc(self.user, "UndoFolder")
        DocumentFolder.objects.create(name="Ziel")
        audit_id = self._apply(doc, "move_to_folder", {"folder": "Ziel"})
        doc.refresh_from_db()
        self.assertIsNotNone(doc.folder_id)

        agent.undo(self.user, audit_id)

        doc.refresh_from_db()
        self.assertIsNone(doc.folder_id)

    def test_double_undo_is_guarded(self):
        doc = _doc(self.user, "UndoTwice")
        audit_id = self._apply(doc, "add_tag", {"tag": "Einmal"})
        self.assertEqual(agent.undo(self.user, audit_id)["status"], "ok")

        self.assertEqual(agent.undo(self.user, audit_id)["status"], "already_undone")

    def test_undo_of_foreign_action_rejected(self):
        doc = _doc(self.user, "UndoForeign")
        audit_id = self._apply(doc, "add_tag", {"tag": "Meins"})

        res = agent.undo(self.other, audit_id)

        self.assertEqual(res["status"], "not_found")
        self.assertTrue(doc.tags.filter(name="Meins").exists())


class AgentPlanTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("agent-p", password="pw", role="user")
        cls.doc = _doc(cls.user, "Stromvertrag")

    def _provider(self, actions):
        provider = mock.Mock()
        provider.available = True
        provider.complete.return_value = json.dumps({"actions": actions})
        return provider

    def test_plan_validates_and_returns_actions(self):
        provider = self._provider(
            [{"action": "add_tag", "document": self.doc.id, "params": {"tag": "Strom"}}]
        )
        with mock.patch(
            "documents.services.hybrid_search.hybrid_search",
            return_value=[{"document": self.doc.id, "document_title": "Stromvertrag"}],
        ), mock.patch("ai.providers.get_provider", return_value=provider):
            result = agent.plan(self.user, "Stromvertrag mit Strom taggen")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["document"], self.doc.id)
        self.assertIn("summary", result["actions"][0])

    def test_plan_drops_noncandidate_and_unknown(self):
        provider = self._provider(
            [
                {"action": "add_tag", "document": 999999, "params": {"tag": "X"}},  # kein Kandidat
                {"action": "nuke", "document": self.doc.id, "params": {}},  # unbekannt
                {"action": "set_note", "document": self.doc.id, "params": {"note": "ok"}},  # gültig
            ]
        )
        with mock.patch(
            "documents.services.hybrid_search.hybrid_search",
            return_value=[{"document": self.doc.id, "document_title": "Stromvertrag"}],
        ), mock.patch("ai.providers.get_provider", return_value=provider):
            result = agent.plan(self.user, "irgendwas")

        actions = result["actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "set_note")

    def test_plan_unavailable_provider(self):
        provider = mock.Mock()
        provider.available = False
        with mock.patch(
            "documents.services.hybrid_search.hybrid_search",
            return_value=[{"document": self.doc.id, "document_title": "Stromvertrag"}],
        ), mock.patch("ai.providers.get_provider", return_value=provider):
            result = agent.plan(self.user, "irgendwas")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["actions"], [])


class AgentApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("agent-api", password="pw", role="user")
        cls.doc = _doc(cls.user)

    def test_execute_endpoint_applies(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            "/api/agent/execute/",
            {"actions": [{"action": "add_tag", "document": self.doc.id, "params": {"tag": "Auto"}}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["applied"]), 1)
        self.assertTrue(self.doc.tags.filter(name="Auto").exists())
