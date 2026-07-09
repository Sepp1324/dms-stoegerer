from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from .models import Document, DocumentEmbedding, DocumentPageText, DocumentVersion
from .services import semantic_index

User = get_user_model()


def make_doc(owner, title, text, *, page_no=1):
    doc = Document.objects.create(title=title, owner=owner)
    version = DocumentVersion.objects.create(
        document=doc,
        version_no=1,
        file_path="/tmp/semantic.pdf",
        sha256="b" * 64,
        ocr_text=text,
    )
    doc.current_version = version
    doc.save(update_fields=["current_version"])
    DocumentPageText.objects.create(version=version, page_no=page_no, text=text)
    return doc


class SemanticIndexServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="semantic-u", password="pw", role="user"
        )

    def test_sync_creates_chunks_and_search_returns_semantic_source(self):
        insurance = make_doc(
            self.user,
            "Helvetia Polizze",
            "Kfz Versicherung mit jährlicher Prämie und Polizzennummer HV-123.",
        )
        invoice = make_doc(
            self.user,
            "Stromrechnung",
            "Stromrechnung mit IBAN und Zahlungsreferenz.",
        )
        semantic_index.sync_document_embeddings(insurance)
        semantic_index.sync_document_embeddings(invoice)

        results = semantic_index.search_documents(
            "Welche Versicherungspolizze gibt es?",
            [insurance, invoice],
            limit=3,
        )

        self.assertEqual(results[0]["document"], insurance.id)
        self.assertEqual(results[0]["source_type"], "semantic")
        self.assertGreater(results[0]["score"], 0)

    def test_similar_documents_uses_only_indexed_visible_documents(self):
        base = make_doc(
            self.user,
            "Wüstenrot Versicherung",
            "Versicherung Polizze monatliche Prämie Vertragsnummer WU-1.",
        )
        similar = make_doc(
            self.user,
            "Helvetia Versicherung",
            "Versicherung Polizze jährliche Prämie Vertragsnummer HE-2.",
        )
        different = make_doc(
            self.user,
            "Gemeinde Bescheid",
            "Bescheid der Gemeinde über Meldebestätigung.",
        )
        for doc in [base, similar, different]:
            semantic_index.sync_document_embeddings(doc)

        results = semantic_index.similar_documents(base, [base, similar, different])

        self.assertTrue(results)
        self.assertEqual(results[0]["document"], similar.id)
        self.assertNotEqual(results[0]["document"], base.id)

    def test_health_counts_missing_documents(self):
        indexed = make_doc(self.user, "Indexiert", "Versicherung Vertrag")
        missing = make_doc(self.user, "Fehlt", "Noch nicht indexiert")
        semantic_index.sync_document_embeddings(indexed)

        health = semantic_index.embedding_health(Document.objects.filter(id__in=[indexed.id, missing.id]))

        self.assertEqual(health["documents"], 2)
        self.assertEqual(health["indexed_documents"], 1)
        self.assertEqual(health["missing_documents"], 1)


class SemanticIndexApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="semantic-api-u", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="semantic-api-o", password="pw", role="user"
        )
        cls.base = make_doc(
            cls.user,
            "Meine Versicherung",
            "Versicherung Polizze Prämie Vertragsnummer OWN-1.",
        )
        cls.own_similar = make_doc(
            cls.user,
            "Zweite Versicherung",
            "Versicherung Polizze Prämie Vertragsnummer OWN-2.",
        )
        cls.foreign_similar = make_doc(
            cls.other,
            "Fremde Versicherung",
            "Versicherung Polizze Prämie Vertragsnummer FOREIGN.",
        )
        for doc in [cls.base, cls.own_similar, cls.foreign_similar]:
            semantic_index.sync_document_embeddings(doc)

    def test_similar_endpoint_is_owner_scoped(self):
        self.client.force_authenticate(self.user)

        resp = self.client.get(f"/api/documents/{self.base.id}/similar/")

        self.assertEqual(resp.status_code, 200)
        ids = [item["document"] for item in resp.data["results"]]
        self.assertIn(self.own_similar.id, ids)
        self.assertNotIn(self.foreign_similar.id, ids)

    def test_reindex_endpoint_rebuilds_chunks_for_writer(self):
        self.client.force_authenticate(self.user)
        DocumentEmbedding.objects.filter(document=self.base).delete()

        resp = self.client.post(f"/api/documents/{self.base.id}/reindex-semantic/")

        self.assertEqual(resp.status_code, 200)
        self.assertGreater(resp.data["created"], 0)
        self.assertTrue(DocumentEmbedding.objects.filter(document=self.base).exists())
