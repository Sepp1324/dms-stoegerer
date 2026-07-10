from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    ContractRecord,
    Document,
    DocumentReminder,
    DocumentReviewTask,
)

User = get_user_model()


class TimelineTests(APITestCase):
    """Fristen-Center aggregiert mehrere Quellen owner-gescopet."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="timeline_owner", password="pw", role="user")
        cls.other = User.objects.create_user(username="timeline_other", password="pw", role="user")
        today = timezone.localdate()

        cls.doc = Document.objects.create(
            title="Haushaltsversicherung",
            owner=cls.owner,
            retention_until=today + timedelta(days=20),
            status=Document.ApprovalStatus.ZUR_FREIGABE,
        )
        cls.foreign = Document.objects.create(
            title="Fremde Kündigung",
            owner=cls.other,
        )

        DocumentReminder.objects.create(
            document=cls.doc,
            remind_on=today - timedelta(days=1),
            note="Kündigungsfenster prüfen",
            created_by=cls.owner,
        )
        ContractRecord.objects.create(
            document=cls.doc,
            provider="Helvetia",
            contract_number="HV-2026",
            contract_type=ContractRecord.ContractType.INSURANCE,
            amount=Decimal("12.50"),
            cancel_until=today + timedelta(days=10),
            next_due_on=today + timedelta(days=25),
            status=ContractRecord.Status.ACTIVE,
            needs_review=False,
        )
        DocumentReviewTask.objects.create(
            document=cls.doc,
            kind=DocumentReviewTask.Kind.CONTRACT_REVIEW,
            signature="timeline:contract",
            priority=10,
            message="Vertrag prüfen.",
        )
        DocumentReminder.objects.create(
            document=cls.foreign,
            remind_on=today,
            note="Fremde Erinnerung",
            created_by=cls.other,
        )

    def test_timeline_aggregiert_quellen_und_ist_owner_scoped(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get("/api/timeline/?days=30")

        self.assertEqual(response.status_code, 200, response.data)
        kinds = {item["kind"] for item in response.data["items"]}
        self.assertIn("reminder_due", kinds)
        self.assertIn("contract_cancel_until", kinds)
        self.assertIn("contract_next_due", kinds)
        self.assertIn("review_contract_review", kinds)
        self.assertIn("approval_pending", kinds)
        self.assertIn("retention_until", kinds)
        self.assertGreaterEqual(response.data["summary"]["overdue"], 1)
        self.assertGreaterEqual(response.data["summary"]["high"], 1)
        self.assertFalse(
            any(item["document"] == self.foreign.id for item in response.data["items"])
        )

    def test_timeline_horizon_filtert_spaetere_termine(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get("/api/timeline/?days=7")

        self.assertEqual(response.status_code, 200, response.data)
        kinds = {item["kind"] for item in response.data["items"]}
        self.assertIn("reminder_due", kinds)
        self.assertNotIn("contract_next_due", kinds)
        self.assertNotIn("retention_until", kinds)

    def test_timeline_ics_export_enthaelt_keine_fremden_termine(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get("/api/timeline/ics/?days=30")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        body = response.content.decode("utf-8")
        self.assertIn("BEGIN:VCALENDAR", body)
        self.assertIn("Haushaltsversicherung", body)
        self.assertIn("Kündigungsfrist", body)
        self.assertNotIn("Fremde Kündigung", body)
