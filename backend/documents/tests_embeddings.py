"""Tests für Chunking + Embedding-Pipeline (semantische Suche).

Die eigentliche Embedding-Berechnung (fastembed) wird gemockt – der CI-Runner soll
kein ~1 GB-Modell herunterladen. Getestet werden Chunking, das Anlegen der Chunks
und die Idempotenz (Re-Embedding ersetzt).
"""
from unittest import mock

from django.test import TestCase

from documents import chunking
from documents.models import (
    EMBEDDING_DIM,
    Document,
    DocumentChunk,
    DocumentVersion,
)
from documents.tasks import embed_document_version


class ChunkingTests(TestCase):
    def test_empty_text(self):
        self.assertEqual(chunking.chunk_text(""), [])
        self.assertEqual(chunking.chunk_text(None), [])

    def test_short_text_single_chunk(self):
        self.assertEqual(chunking.chunk_text("kurzer Text"), ["kurzer Text"])

    def test_long_text_overlapping_chunks(self):
        text = "abcdefghij" * 300  # 3000 Zeichen
        chunks = chunking.chunk_text(text, max_chars=1000, overlap=150)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))


class EmbedTaskTests(TestCase):
    def _doc_with_text(self, text):
        doc = Document.objects.create(title="Doc")
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path="/data/originals/x.pdf",
            ocr_text=text,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc, version

    @mock.patch("ai.embeddings.embed_passages")
    @mock.patch("ai.embeddings.enabled", return_value=True)
    def test_creates_chunks_with_embeddings(self, _enabled, embed):
        _doc, version = self._doc_with_text("Wort " * 400)  # ~2000 Zeichen
        expected = chunking.chunk_text(version.ocr_text)
        embed.return_value = [[0.1] * EMBEDDING_DIM for _ in expected]

        result = embed_document_version(version.id)

        self.assertEqual(result["status"], "indexed")
        self.assertEqual(
            DocumentChunk.objects.filter(version=version).count(), len(expected)
        )
        embed.assert_called_once()

    @mock.patch("ai.embeddings.enabled", return_value=True)
    def test_empty_text_creates_no_chunks(self, _enabled):
        _doc, version = self._doc_with_text("")

        result = embed_document_version(version.id)

        self.assertEqual(result["status"], "empty")
        self.assertEqual(DocumentChunk.objects.count(), 0)

    @mock.patch("ai.embeddings.embed_passages")
    @mock.patch("ai.embeddings.enabled", return_value=True)
    def test_reembedding_replaces_chunks(self, _enabled, embed):
        _doc, version = self._doc_with_text("Text " * 400)
        expected = chunking.chunk_text(version.ocr_text)
        embed.return_value = [[0.1] * EMBEDDING_DIM for _ in expected]

        embed_document_version(version.id)
        embed_document_version(version.id)  # erneut → ersetzt, keine Duplikate

        self.assertEqual(
            DocumentChunk.objects.filter(version=version).count(), len(expected)
        )

    @mock.patch("ai.embeddings.enabled", return_value=False)
    def test_disabled_is_noop(self, _enabled):
        _doc, version = self._doc_with_text("Wort " * 400)

        result = embed_document_version(version.id)

        self.assertEqual(result["status"], "disabled")
        self.assertEqual(DocumentChunk.objects.count(), 0)
