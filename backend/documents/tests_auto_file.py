"""Tests für die Auto-Ablage (kNN-Vorschläge über Embeddings).

Nutzt denselben deterministischen Fake-Embedder wie tests_semantic_index (kein
1-GB-Modell im CI). Geprüft werden das Voting (Ordner/Tags/Korrespondent/Typ aus
den Nachbarn), Owner-Scoping, „füllt nur leere Felder" und die Endpunkte.
"""
import hashlib
import math
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from .models import (
    Correspondent,
    Document,
    DocumentFolder,
    DocumentType,
    DocumentVersion,
    Tag,
)
from .services import auto_file, semantic_index

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
    # Fake-Embedder liegt auf anderer Skala als e5 → Nachbar-Floor absenken.
    _patchers.append(mock.patch.object(auto_file, "MIN_NEIGHBOR_SIMILARITY", 0.1))
    for patcher in _patchers:
        patcher.start()


def tearDownModule():
    for patcher in _patchers:
        patcher.stop()
    _patchers.clear()


def make_doc(owner, title, text, **filing):
    doc = Document.objects.create(title=title, owner=owner, **{
        k: v for k, v in filing.items() if k != "tags"
    })
    version = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path="/tmp/af.pdf",
        sha256="c" * 64,
        ocr_text=text,
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    if filing.get("tags"):
        doc.tags.add(*filing["tags"])
    semantic_index.sync_document_embeddings(doc)
    return doc


class AutoFileServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="af-u", password="pw", role="user")
        cls.other = User.objects.create_user(username="af-o", password="pw", role="user")
        cls.folder = DocumentFolder.objects.create(name="Versicherungen")
        cls.correspondent = Correspondent.objects.create(name="Helvetia")
        cls.dtype = DocumentType.objects.create(name="Polizze")
        cls.tag = Tag.objects.create(name="KFZ")

        # Drei bereits abgelegte Nachbarn zum selben Thema, gleiche Ablage.
        for i in range(3):
            make_doc(
                cls.user,
                f"Kfz-Polizze {i}",
                "Kfz Versicherung Polizze jährliche Prämie Vertragsnummer HV.",
                folder=cls.folder,
                correspondent=cls.correspondent,
                document_type=cls.dtype,
                tags=[cls.tag],
            )
        # Fremdes Dokument (anderer Owner) zum selben Thema, andere Ablage.
        cls.foreign = make_doc(
            cls.other,
            "Fremde Polizze",
            "Kfz Versicherung Polizze jährliche Prämie Vertragsnummer XX.",
            folder=DocumentFolder.objects.create(name="Fremd"),
        )
        # Ziel: gleiches Thema, noch UNabgelegt.
        cls.target = make_doc(
            cls.user,
            "Neue Kfz-Police",
            "Kfz Versicherung Polizze jährliche Prämie Vertragsnummer NEU.",
        )

    def _visible(self):
        return Document.objects.filter(owner=self.user)

    def test_suggests_folder_tags_correspondent_type_from_neighbors(self):
        result = auto_file.suggest_filing(self.target, self._visible())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["folder"]["id"], self.folder.id)
        self.assertEqual(result["correspondent"]["id"], self.correspondent.id)
        self.assertEqual(result["document_type"]["id"], self.dtype.id)
        self.assertIn(self.tag.id, [t["id"] for t in result["tags"]])
        # Confidence der einstimmigen Nachbarn ist hoch.
        self.assertGreaterEqual(result["folder"]["confidence"], 0.9)

    def test_owner_scoping_excludes_foreign_neighbors(self):
        # Nur eigener Bestand sichtbar → das fremde Dokument darf nicht mitwählen.
        result = auto_file.suggest_filing(self.target, self._visible())
        neighbor_ids = [n["document"] for n in result["neighbors"]]
        self.assertNotIn(self.foreign.id, neighbor_ids)

    def test_apply_fills_empty_fields_and_adds_tags(self):
        suggestion = auto_file.suggest_filing(self.target, self._visible())
        changed = auto_file.apply_filing(self.target, suggestion)

        self.target.refresh_from_db()
        self.assertEqual(self.target.folder_id, self.folder.id)
        self.assertEqual(self.target.correspondent_id, self.correspondent.id)
        self.assertEqual(self.target.document_type_id, self.dtype.id)
        self.assertIn(self.tag.id, list(self.target.tags.values_list("id", flat=True)))
        self.assertIn("folder", changed)
        self.assertIn("tags", changed)

    def test_apply_does_not_overwrite_manual_folder(self):
        manual = DocumentFolder.objects.create(name="Manuell")
        self.target.folder = manual
        self.target.save(update_fields=["folder"])

        suggestion = auto_file.suggest_filing(self.target, self._visible())
        auto_file.apply_filing(self.target, suggestion)

        self.target.refresh_from_db()
        self.assertEqual(self.target.folder_id, manual.id)


class AutoFileApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="af-api", password="pw", role="user")
        cls.folder = DocumentFolder.objects.create(name="Steuer")
        cls.tag = Tag.objects.create(name="Finanzamt")
        for i in range(2):
            make_doc(
                cls.user,
                f"Bescheid {i}",
                "Einkommensteuerbescheid Finanzamt Steuernummer Nachzahlung.",
                folder=cls.folder,
                tags=[cls.tag],
            )
        cls.target = make_doc(
            cls.user,
            "Neuer Bescheid",
            "Einkommensteuerbescheid Finanzamt Steuernummer Guthaben.",
        )

    def test_filing_suggestions_endpoint(self):
        self.client.force_authenticate(self.user)

        resp = self.client.get(f"/api/documents/{self.target.id}/filing-suggestions/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "ok")
        self.assertEqual(resp.data["folder"]["id"], self.folder.id)

    def test_apply_filing_endpoint_applies_and_audits(self):
        self.client.force_authenticate(self.user)

        resp = self.client.post(f"/api/documents/{self.target.id}/apply-filing/")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("folder", resp.data["applied"])
        self.target.refresh_from_db()
        self.assertEqual(self.target.folder_id, self.folder.id)
