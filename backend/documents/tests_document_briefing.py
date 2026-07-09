from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    CaseFile,
    ContractRecord,
    Correspondent,
    Document,
    DocumentEntity,
    DocumentReminder,
    DocumentReviewTask,
    DocumentVersion,
    KnowledgeEntity,
    OCRStatus,
)

User = get_user_model()


class DocumentBriefingTests(APITestCase):
    """Dokument-Briefing bündelt vorhandene Signale ohne externen KI-Provider."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="brief_owner", password="pw", role="user")
        cls.other = User.objects.create_user(username="brief_other", password="pw", role="user")
        cls.correspondent = Correspondent.objects.create(name="Helvetia")
        cls.case_file = CaseFile.objects.create(title="Versicherungen 2026", owner=cls.owner)
        cls.document = cls._document(
            "Polizze Haushalt",
            owner=cls.owner,
            correspondent=cls.correspondent,
            case_file=cls.case_file,
            ai_suggestions={"summary": "Haushaltsversicherung mit offener Vertragsprüfung."},
        )
        cls.related = cls._document(
            "Vorjahrespolizze",
            owner=cls.owner,
            correspondent=cls.correspondent,
            case_file=cls.case_file,
        )
        cls.foreign = cls._document("Fremdes Dokument", owner=cls.other)

        DocumentReviewTask.objects.create(
            document=cls.document,
            kind=DocumentReviewTask.Kind.CONTRACT_REVIEW,
            signature="contract:test",
            priority=10,
            message="Vertragsdaten müssen bestätigt werden.",
            suggested_action="Vertrag prüfen",
        )
        DocumentReminder.objects.create(
            document=cls.document,
            remind_on=timezone.localdate() - timedelta(days=1),
            note="Kündigungsfrist prüfen",
            created_by=cls.owner,
        )
        ContractRecord.objects.create(
            document=cls.document,
            case_file=cls.case_file,
            contract_type=ContractRecord.ContractType.INSURANCE,
            provider="Helvetia",
            contract_number="HV-123",
            amount=Decimal("12.50"),
            cancel_until=timezone.localdate() + timedelta(days=20),
            next_due_on=timezone.localdate() + timedelta(days=30),
            status=ContractRecord.Status.ACTIVE,
            needs_review=True,
        )
        entity = KnowledgeEntity.objects.create(
            owner=cls.owner,
            kind=KnowledgeEntity.Kind.COMPANY,
            name="Helvetia Versicherungen AG",
            canonical_name="helvetia versicherungen ag",
            confidence=90,
        )
        DocumentEntity.objects.create(
            document=cls.document,
            entity=entity,
            role=DocumentEntity.Role.CONTRACT,
            confidence=92,
            occurrences=3,
        )

    @classmethod
    def _document(cls, title, *, owner, correspondent=None, case_file=None, ai_suggestions=None):
        doc = Document.objects.create(
            title=title,
            owner=owner,
            correspondent=correspondent,
            case_file=case_file,
            ai_suggestions=ai_suggestions or {},
        )
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=f"/data/originals/{title}.pdf",
            sha256=(title.encode().hex() * 4).ljust(64, "0")[:64],
            processing_state=DocumentVersion.ProcessingState.READY,
            ocr_status=OCRStatus.SUCCESS,
            ocr_text=(
                "Haushaltsversicherung Helvetia. "
                "Der Vertrag hat eine Kündigungsfrist und eine nächste Fälligkeit."
            ),
            page_count=2,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def test_briefing_endpoint_verdichtet_signale_und_naechste_aktionen(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(f"/api/documents/{self.document.id}/briefing/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["document"]["id"], self.document.id)
        self.assertEqual(response.data["summary"]["source"], "ai_suggestions")
        self.assertEqual(response.data["risk_level"], "high")
        self.assertGreaterEqual(response.data["metadata_score"]["percent"], 50)

        action_kinds = {action["kind"] for action in response.data["next_actions"]}
        self.assertIn("review_task:contract_review", action_kinds)
        self.assertIn("reminder_due", action_kinds)
        self.assertIn("contract_review", action_kinds)

        self.assertEqual(response.data["signals"]["contract"]["provider"], "Helvetia")
        self.assertEqual(response.data["signals"]["review_tasks"][0]["priority"], 10)
        self.assertTrue(response.data["signals"]["reminders"][0]["due"])
        self.assertEqual(
            response.data["relations"]["entities"][0]["name"],
            "Helvetia Versicherungen AG",
        )
        self.assertEqual(
            response.data["relations"]["related_documents"][0]["id"],
            self.related.id,
        )

    def test_briefing_ist_owner_scoped(self):
        self.client.force_authenticate(self.other)

        response = self.client.get(f"/api/documents/{self.document.id}/briefing/")

        self.assertEqual(response.status_code, 404)
