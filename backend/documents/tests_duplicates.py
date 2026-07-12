"""Tests für die Dubletten-/Versionserkennung (Cosine über Embeddings).

Deterministischer Fake-Embedder (kein 1-GB-Modell im CI): identischer Text ergibt
identische Vektoren (Cosine 1.0 → „duplicate"), inhaltlich fremde Dokumente liegen
klar unter der Schwelle.
"""
import hashlib
import math
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from .models import Document, DocumentVersion
from .services import duplicates, semantic_index

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
    for patcher in _patchers:
        patcher.start()


def tearDownModule():
    for patcher in _patchers:
        patcher.stop()
    _patchers.clear()


def make_doc(owner, title, text):
    doc = Document.objects.create(title=title, owner=owner)
    version = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path=f"/tmp/{title}.pdf",
        sha256=hashlib.sha256(title.encode("utf-8")).hexdigest(),
        ocr_text=text,
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    semantic_index.sync_document_embeddings(doc)
    return doc


# Identischer Beleg (Re-Scan) vs. inhaltlich fremdes Dokument.
INVOICE = "Stromrechnung Januar Betrag IBAN Zahlungsreferenz Kundennummer 4711."
PASSPORT = "Reisepass Personalausweis Bürgeramt Ausweisnummer Meldebestätigung."


class DuplicateServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="dup-u", password="pw", role="user")
        cls.other = User.objects.create_user(username="dup-o", password="pw", role="user")
        cls.original = make_doc(cls.user, "Stromrechnung", INVOICE)
        cls.rescan = make_doc(cls.user, "Stromrechnung (Scan 2)", INVOICE)
        cls.unrelated = make_doc(cls.user, "Reisepass", PASSPORT)
        cls.foreign = make_doc(cls.other, "Fremde Rechnung", INVOICE)

    def _visible(self):
        return Document.objects.filter(owner=self.user)

    def test_detects_rescan_as_duplicate(self):
        res = duplicates.find_duplicates(self.original, self._visible())

        self.assertEqual(res["status"], "ok")
        ids = [r["document"] for r in res["results"]]
        self.assertIn(self.rescan.id, ids)
        self.assertNotIn(self.unrelated.id, ids)
        hit = next(r for r in res["results"] if r["document"] == self.rescan.id)
        self.assertEqual(hit["kind"], "duplicate")
        self.assertGreaterEqual(hit["score"], 0.97)

    def test_owner_scoping_excludes_foreign(self):
        res = duplicates.find_duplicates(self.original, self._visible())
        self.assertNotIn(self.foreign.id, [r["document"] for r in res["results"]])

    def test_report_finds_the_pair(self):
        report = duplicates.duplicate_report(self._visible())

        self.assertEqual(report["status"], "ok")
        self.assertGreaterEqual(report["count"], 1)
        pair = report["pairs"][0]
        self.assertEqual(
            {pair["a"], pair["b"]}, {self.original.id, self.rescan.id}
        )
        self.assertEqual(pair["kind"], "duplicate")


class DuplicateApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="dup-api", password="pw", role="user")
        cls.original = make_doc(cls.user, "Beleg", INVOICE)
        cls.rescan = make_doc(cls.user, "Beleg (erneut)", INVOICE)

    def test_duplicates_endpoint(self):
        self.client.force_authenticate(self.user)

        resp = self.client.get(f"/api/documents/{self.original.id}/duplicates/")

        self.assertEqual(resp.status_code, 200)
        ids = [r["document"] for r in resp.data["results"]]
        self.assertIn(self.rescan.id, ids)

    def test_duplicate_report_endpoint(self):
        self.client.force_authenticate(self.user)

        resp = self.client.get("/api/documents/duplicate-report/")

        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.data["count"], 1)
