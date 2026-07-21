"""Tests für den reindex_embeddings-Command.

Kern von P2(b): sync_document_embeddings meldet Modellfehler als RÜCKGABE
({"status": "error"}), nicht als Exception. Der Command muss das als Fehler
zählen und mit non-zero Exit enden – sonst erscheint ein fehlgeschlagener
Reindex als "0 Chunks, 0 Fehler" mit Exitcode 0.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import Document, DocumentVersion

User = get_user_model()


class ReindexEmbeddingsExitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reindex", password="pw")
        doc = Document.objects.create(title="d", owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path="/tmp/reindex.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            ocr_text="etwas Text",
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])

    def test_status_error_wird_als_fehler_gewertet_und_exit_nonzero(self):
        with mock.patch(
            "documents.management.commands.reindex_embeddings.embeddings.enabled",
            return_value=True,
        ), mock.patch(
            "documents.services.semantic_index.sync_document_embeddings",
            return_value={"status": "error", "created": 0},
        ):
            with self.assertRaises(CommandError):
                call_command("reindex_embeddings", "--all")

    def test_erfolg_wirft_nicht(self):
        with mock.patch(
            "documents.management.commands.reindex_embeddings.embeddings.enabled",
            return_value=True,
        ), mock.patch(
            "documents.services.semantic_index.sync_document_embeddings",
            return_value={"status": "indexed", "created": 3},
        ):
            # Kein CommandError -> Exitcode 0.
            call_command("reindex_embeddings", "--all")

    def test_deaktivierte_embeddings_fail_fast_nonzero(self):
        # Embeddings global aus -> sofort CommandError (kein irreführender Exit 0).
        with mock.patch(
            "documents.management.commands.reindex_embeddings.embeddings.enabled",
            return_value=False,
        ):
            with self.assertRaises(CommandError):
                call_command("reindex_embeddings", "--all")

    def test_dry_run_funktioniert_trotz_deaktivierter_embeddings(self):
        # --dry-run zeigt nur an und braucht keine aktivierten Embeddings.
        with mock.patch(
            "documents.management.commands.reindex_embeddings.embeddings.enabled",
            return_value=False,
        ):
            # Kein CommandError.
            call_command("reindex_embeddings", "--all", "--dry-run")
