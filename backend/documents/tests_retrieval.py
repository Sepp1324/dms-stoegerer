from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (
    ContractRecord,
    Document,
    DocumentEntity,
    DocumentPageText,
    DocumentVersion,
    KnowledgeEntity,
)
from .services import retrieval

User = get_user_model()


class RetrievalServiceTests(TestCase):
    """Copilot-Retrieval: Quellen aus Text, Entitäten und Verträgen."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="retrieval-user", password="pw", role="user"
        )

    def _doc(self, title, text):
        doc = Document.objects.create(title=title, owner=self.user)
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path="/tmp/retrieval.pdf",
            sha256="a" * 64,
            ocr_text=text,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_entity_graph_match_is_returned_even_without_text_hit(self):
        doc = self._doc("Versicherungsunterlage", "Allgemeine Vertragsbedingungen.")
        entity = KnowledgeEntity.objects.create(
            owner=self.user,
            kind=KnowledgeEntity.Kind.PERSON,
            name="Cornelia Stögerer",
            canonical_name="cornelia stoegerer",
            source=KnowledgeEntity.Source.MANUAL,
            confidence=100,
        )
        DocumentEntity.objects.create(
            document=doc,
            entity=entity,
            role=DocumentEntity.Role.MENTION,
            source=KnowledgeEntity.Source.MANUAL,
            confidence=100,
        )

        context = retrieval.retrieve_context("Welche Dokumente gibt es zu Cornelia?", [doc])

        self.assertEqual(context["sources"][0]["document"], doc.id)
        self.assertEqual(context["sources"][0]["source_type"], "metadata")
        self.assertEqual(context["sources"][0]["entities"][0]["name"], "Cornelia Stögerer")
        self.assertIn("cornelia", context["sources"][0]["matched_terms"])

    def test_contract_metadata_answers_expiry_questions(self):
        doc = self._doc("Helvetia Polizze", "Allgemeine Bedingungen ohne Jahreszahl.")
        ContractRecord.objects.create(
            document=doc,
            contract_type=ContractRecord.ContractType.INSURANCE,
            provider="Helvetia Versicherungen AG",
            contract_number="POL-2026-77",
            status=ContractRecord.Status.ACTIVE,
            ends_on=date(2026, 12, 31),
            cancel_until=date(2026, 9, 30),
        )

        context = retrieval.retrieve_context("Welche Verträge laufen 2026 aus?", [doc])

        source = context["sources"][0]
        self.assertEqual(source["document"], doc.id)
        self.assertEqual(source["contract"]["provider"], "Helvetia Versicherungen AG")
        self.assertEqual(source["contract"]["ends_on"], "2026-12-31")
        self.assertIn("vertrag", source["matched_terms"])

    def test_page_text_hit_keeps_page_number_and_snippet(self):
        doc = self._doc("Wüstenrot", "Kompletter OCR-Fallback.")
        DocumentPageText.objects.create(
            version=doc.current_version,
            page_no=3,
            text="Die monatliche Prämie beträgt 225,74 Euro.",
        )

        context = retrieval.retrieve_context("Wie hoch ist die Prämie?", [doc])

        source = context["sources"][0]
        self.assertEqual(source["page"], 3)
        self.assertEqual(source["source_type"], "page_text")
        self.assertIn("225,74 Euro", source["snippet"])
