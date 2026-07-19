"""Tests für das Laden/Fehler-Caching des Embedding-Modells (Incident-Lehre)."""
from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

import ai.embeddings as emb


class EmbeddingModelLoadTests(SimpleTestCase):
    def setUp(self):
        emb._model = None
        emb._load_error = None

    def tearDown(self):
        emb._model = None
        emb._load_error = None

    def test_load_failure_is_cached_and_not_retried(self):
        with mock.patch(
            "fastembed.TextEmbedding", side_effect=OSError("model.onnx fehlt")
        ) as tm:
            with self.assertRaises(emb.EmbeddingModelUnavailable):
                emb._get_model()
            # Zweiter Aufruf: knapp fehlschlagen OHNE erneuten Ladeversuch.
            with self.assertRaises(emb.EmbeddingModelUnavailable):
                emb._get_model()
        self.assertEqual(tm.call_count, 1)

    def test_success_caches_singleton(self):
        fake = mock.Mock()
        with mock.patch("fastembed.TextEmbedding", return_value=fake) as tm:
            self.assertIs(emb._get_model(), fake)
            self.assertIs(emb._get_model(), fake)
        self.assertEqual(tm.call_count, 1)
