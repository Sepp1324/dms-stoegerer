from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    ContractRecord,
    Correspondent,
    Document,
    DocumentReminder,
    DocumentReviewTask,
    DocumentVersion,
)
from .services import contracts

User = get_user_model()


class ContractCenterTests(APITestCase):
    """Contract Center: Erkennung, Review-Aufgaben und Owner-Scope."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="contract_owner", password="pw", role="user"
        )
        cls.other = User.objects.create_user(
            username="contract_other", password="pw", role="user"
        )
        cls.guest = User.objects.create_user(
            username="contract_guest", password="pw", role="guest"
        )
        cls.correspondent = Correspondent.objects.create(name="Wüstenrot Gruppe")

    def _doc(self, title, owner, text, *, correspondent=None):
        doc = Document.objects.create(
            title=title,
            owner=owner,
            correspondent=correspondent,
        )
        version = DocumentVersion.objects.create(
            document=doc,
            version_no=1,
            file_path=f"/tmp/contract-{doc.id}.pdf",
            sha256=f"{doc.id:064x}"[-64:],
            ocr_text=text,
            processing_state=DocumentVersion.ProcessingState.READY,
        )
        doc.current_version = version
        doc.save(update_fields=["current_version"])
        return doc

    def _full_contract_text(self):
        future_year = timezone.now().date().year + 2
        return f"""
        WÜSTENROT GRUPPE
        Vertragsnummer 510/839294-2
        Mobilitätsversicherung
        Beitrag 225,74 EUR monatlich
        Vertragsbeginn 01.01.{future_year}
        Vertragsende 31.12.{future_year}
        Kündigungsfrist 3 Monate
        nächste Fälligkeit 15.01.{future_year}
        """

    def test_sync_contract_record_extracts_core_fields_and_reminders(self):
        doc = self._doc(
            "Wüstenrot Mobilitätsversicherung",
            self.owner,
            self._full_contract_text(),
            correspondent=self.correspondent,
        )

        result = contracts.sync_contract_record(doc, actor=self.owner)

        self.assertEqual(result["status"], "created")
        record = ContractRecord.objects.get(document=doc)
        future_year = timezone.now().date().year + 2
        self.assertEqual(record.provider, "Wüstenrot Gruppe")
        self.assertEqual(record.contract_number, "510/839294-2")
        self.assertEqual(record.contract_type, ContractRecord.ContractType.INSURANCE)
        self.assertEqual(record.amount, Decimal("225.74"))
        self.assertEqual(record.billing_cycle, ContractRecord.BillingCycle.MONTHLY)
        self.assertEqual(record.ends_on, date(future_year, 12, 31))
        self.assertEqual(record.cancel_until, date(future_year, 12, 31) - timedelta(days=90))
        self.assertFalse(record.needs_review)
        self.assertEqual(DocumentReminder.objects.filter(document=doc).count(), 2)

    def test_uncertain_contract_creates_review_task_and_confirm_resolves_it(self):
        future_year = timezone.now().date().year + 2
        doc = self._doc(
            "Versicherung ohne Nummer",
            self.owner,
            f"""
            Versicherung Beitrag 49,90 EUR monatlich.
            Vertragsende 31.10.{future_year}
            Kündigungsfrist 2 Monate.
            """,
        )
        contracts.sync_contract_record(doc, actor=self.owner)
        record = ContractRecord.objects.get(document=doc)
        self.assertTrue(record.needs_review)
        task = DocumentReviewTask.objects.get(
            document=doc,
            kind=DocumentReviewTask.Kind.CONTRACT_REVIEW,
        )
        self.assertEqual(task.status, DocumentReviewTask.Status.OPEN)

        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/contracts/{record.id}/confirm/")

        self.assertEqual(resp.status_code, 200)
        record.refresh_from_db()
        task.refresh_from_db()
        self.assertFalse(record.needs_review)
        self.assertEqual(task.status, DocumentReviewTask.Status.RESOLVED)

    def test_rescan_does_not_reopen_manually_confirmed_contract(self):
        future_year = timezone.now().date().year + 2
        doc = self._doc(
            "Bestätigter Vertrag",
            self.owner,
            f"""
            Versicherung Beitrag 49,90 EUR monatlich.
            Vertragsende 31.10.{future_year}
            Kündigungsfrist 2 Monate.
            """,
        )
        contracts.sync_contract_record(doc, actor=self.owner)
        record = ContractRecord.objects.get(document=doc)
        contracts.confirm_contract(record, actor=self.owner)

        contracts.sync_contract_record(doc, actor=self.owner)

        record.refresh_from_db()
        self.assertFalse(record.needs_review)
        self.assertEqual(record.source, ContractRecord.Source.MANUAL)
        self.assertEqual(
            DocumentReviewTask.objects.get(
                document=doc, kind=DocumentReviewTask.Kind.CONTRACT_REVIEW
            ).status,
            DocumentReviewTask.Status.RESOLVED,
        )

    def test_contract_api_is_owner_scoped(self):
        mine = self._doc("Mein Vertrag", self.owner, self._full_contract_text())
        foreign = self._doc("Fremder Vertrag", self.other, self._full_contract_text())
        ContractRecord.objects.create(document=mine, provider="Mein Anbieter")
        foreign_record = ContractRecord.objects.create(
            document=foreign, provider="Fremder Anbieter"
        )

        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/contracts/")

        self.assertEqual(resp.status_code, 200)
        providers = {item["provider"] for item in resp.data["results"]}
        self.assertEqual(providers, {"Mein Anbieter"})

        resp = self.client.get(f"/api/contracts/{foreign_record.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_scan_only_processes_visible_documents(self):
        mine = self._doc("Mein Scan", self.owner, self._full_contract_text())
        foreign = self._doc("Fremder Scan", self.other, self._full_contract_text())

        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            "/api/contracts/scan/",
            {"ids": [mine.id, foreign.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["scanned"], 1)
        self.assertTrue(ContractRecord.objects.filter(document=mine).exists())
        self.assertFalse(ContractRecord.objects.filter(document=foreign).exists())

    def test_guest_cannot_scan_contracts(self):
        self.client.force_authenticate(self.guest)
        resp = self.client.post("/api/contracts/scan/", {}, format="json")
        self.assertEqual(resp.status_code, 403)
