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


class SupersedeApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="sup-api", password="pw", role="user")
        cls.canonical = make_doc(cls.user, "Original", INVOICE)
        cls.dup = make_doc(cls.user, "Dublette", INVOICE)

    def _list_ids(self, query=""):
        resp = self.client.get(f"/api/documents/{query}")
        return [d["id"] for d in resp.data["results"]]

    def test_supersede_hides_from_list_and_links(self):
        self.client.force_authenticate(self.user)

        resp = self.client.post(
            f"/api/documents/{self.dup.id}/supersede/",
            {"by": self.canonical.id},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.dup.refresh_from_db()
        self.assertEqual(self.dup.superseded_by_id, self.canonical.id)
        # aus der Standardliste ausgeblendet, kanonisches bleibt sichtbar
        ids = self._list_ids()
        self.assertNotIn(self.dup.id, ids)
        self.assertIn(self.canonical.id, ids)
        # explizit anzeigbar
        self.assertIn(self.dup.id, self._list_ids("?include_superseded=1"))
        # kanonisches zählt die Dublette
        detail = self.client.get(f"/api/documents/{self.canonical.id}/")
        self.assertEqual(detail.data["supersedes_count"], 1)

    def test_unsupersede_restores(self):
        self.client.force_authenticate(self.user)
        self.client.post(
            f"/api/documents/{self.dup.id}/supersede/",
            {"by": self.canonical.id},
            format="json",
        )

        resp = self.client.post(f"/api/documents/{self.dup.id}/unsupersede/")

        self.assertEqual(resp.status_code, 200)
        self.dup.refresh_from_db()
        self.assertIsNone(self.dup.superseded_by_id)
        self.assertIn(self.dup.id, self._list_ids())

    def test_cannot_supersede_self(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            f"/api/documents/{self.dup.id}/supersede/",
            {"by": self.dup.id},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_superseded_excluded_from_duplicate_candidates(self):
        self.client.force_authenticate(self.user)
        self.client.post(
            f"/api/documents/{self.dup.id}/supersede/",
            {"by": self.canonical.id},
            format="json",
        )

        resp = self.client.get(f"/api/documents/{self.canonical.id}/duplicates/")

        ids = [r["document"] for r in resp.data["results"]]
        self.assertNotIn(self.dup.id, ids)
