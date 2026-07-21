"""Folgeaktionen laufen nur bei erfolgreichem Pipeline-Lauf (status="done").

Bei FAILED/superseded dürfen weder die internen Syncs (Suchvektor/Entitäten/
Review) noch der KI-Metadaten-Task laufen – sonst verfrühte Vorschläge,
inkonsistente Review-Aufgaben und unnötige API-Kosten (P2).
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import pipeline, tasks
from .models import Document, DocumentVersion

User = get_user_model()


class FollowupGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fg", password="pw")
        doc = Document.objects.create(title="d", owner=self.user)
        self.version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path="/tmp/fg.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            created_by=self.user,
        )

    # --- Task-Ebene: KI-Metadaten nur bei done -------------------------------
    def test_process_document_version_failed_stoesst_keine_ki_an(self):
        with mock.patch.object(
            pipeline, "process_version", return_value={"status": "failed"}
        ), mock.patch("ai.tasks.suggest_document_metadata.delay") as delay:
            tasks.process_document_version(self.version.id)
        delay.assert_not_called()

    def test_process_document_version_done_stoesst_ki_an(self):
        with mock.patch.object(
            pipeline, "process_version", return_value={"status": "done"}
        ), mock.patch("ai.tasks.suggest_document_metadata.delay") as delay:
            tasks.process_document_version(self.version.id)
        delay.assert_called_once()

    def test_superseded_stoesst_keine_ki_an(self):
        with mock.patch.object(
            pipeline, "process_version", return_value={"status": "superseded"}
        ), mock.patch("ai.tasks.suggest_document_metadata.delay") as delay:
            tasks.process_document_version(self.version.id)
        delay.assert_not_called()

    # --- Pipeline-Ebene: interne Syncs nur bei done --------------------------
    def test_process_version_failed_ueberspringt_syncs(self):
        with mock.patch.object(
            pipeline, "_run_from", return_value={"status": "failed"}
        ), mock.patch.object(pipeline, "_sync_semantic_index") as sem:
            pipeline.process_version(self.version)
        sem.assert_not_called()

    def test_process_version_done_faehrt_syncs(self):
        with mock.patch.object(
            pipeline, "_run_from", return_value={"status": "done"}
        ), mock.patch.object(pipeline, "_sync_semantic_index") as sem, mock.patch.object(
            pipeline, "_sync_contract_center"
        ), mock.patch.object(pipeline, "_sync_entity_graph"), mock.patch.object(
            pipeline, "_sync_search_vector"
        ), mock.patch.object(pipeline, "_sync_auto_file"):
            pipeline.process_version(self.version)
        sem.assert_called_once()
