"""Tests für die hybride Suche (FTS + Semantik via RRF)."""
import hashlib
import math
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from documents.models import Document, DocumentVersion
from documents.services import semantic_index

User = get_user_model()

_DIM = settings.EMBEDDING_DIM


def _fake_vector(text: str) -> list[float]:
    vector = [0.0] * _DIM
    for token in semantic_index.tokenize(text):
        bucket = int(hashlib.blake2b(token.encode("utf-8"), digest_size=8).hexdigest(), 16)
        vector[bucket % _DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def _fake_passages(texts):
    return [_fake_vector(t) for t in texts]


_patchers = []


def setUpModule():
    _patchers.append(mock.patch("ai.embeddings.enabled", return_value=True))
    _patchers.append(mock.patch("ai.embeddings.embed_passages", side_effect=_fake_passages))
    _patchers.append(mock.patch("ai.embeddings.embed_query", side_effect=_fake_vector))
    _patchers.append(mock.patch.object(semantic_index, "MIN_SIMILARITY", 0.1))
    for patcher in _patchers:
        patcher.start()


def tearDownModule():
    for patcher in _patchers:
        patcher.stop()
    _patchers.clear()


def _doc(owner, title, text):
    doc = Document.objects.create(title=title, owner=owner)
    version = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path=f"/tmp/{title}.pdf",
        sha256=hashlib.sha256(title.encode()).hexdigest(),
        ocr_text=text,
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    semantic_index.sync_document_embeddings(doc)
    return doc


class HybridSearchTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user("h_alice", password="pw", role="user")
        cls.bob = User.objects.create_user("h_bob", password="pw", role="user")
        cls.insurance = _doc(
            cls.alice, "Autoversicherung", "Kfz Versicherung Polizze jährliche Prämie."
        )
        cls.invoice = _doc(
            cls.alice, "Stromrechnung", "Strom Rechnung Betrag IBAN Kundennummer."
        )

    def test_hybrid_finds_and_labels_sources(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.post(
            "/api/search/hybrid/", {"q": "Versicherung Polizze"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        by_doc = {r["document"]: r for r in resp.data["results"]}
        self.assertIn(self.insurance.id, by_doc)
        # Der Versicherungs-Beleg wird von Volltext UND Semantik getroffen.
        self.assertEqual(
            sorted(by_doc[self.insurance.id]["sources"]), ["fts", "semantic"]
        )
        # Top-Treffer ist der Versicherungs-Beleg (nicht die Stromrechnung).
        self.assertEqual(resp.data["results"][0]["document"], self.insurance.id)

    def test_owner_scoped(self):
        self.client.force_authenticate(self.bob)
        resp = self.client.post(
            "/api/search/hybrid/", {"q": "Versicherung Polizze"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        ids = [r["document"] for r in resp.data["results"]]
        self.assertNotIn(self.insurance.id, ids)

    def test_short_query_rejected(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.post("/api/search/hybrid/", {"q": "ab"}, format="json")
        self.assertEqual(resp.status_code, 400)
