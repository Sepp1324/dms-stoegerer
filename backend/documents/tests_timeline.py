from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    AuditLogEntry,
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

    def test_offene_freigabe_nutzt_einreichungsdatum_nicht_upload(self):
        # P2: Ein Monate ALT hochgeladenes, aber HEUTE eingereichtes Dokument darf
        # nicht sofort als monatelang überfällig erscheinen. Maßgeblich ist der
        # jüngste submit-Audit, nicht added_at.
        today = timezone.localdate()
        doc = Document.objects.create(
            title="Alt hochgeladen, heute eingereicht",
            owner=self.owner,
            status=Document.ApprovalStatus.ZUR_FREIGABE,
        )
        Document.objects.filter(pk=doc.pk).update(
            added_at=timezone.now() - timedelta(days=200)
        )
        AuditLogEntry.objects.create(
            actor=self.owner,
            action="submit",
            object_type="Document",
            object_id=str(doc.id),
            detail={"to": "zur_freigabe"},
        )
        self.client.force_authenticate(self.owner)

        resp = self.client.get("/api/timeline/?days=30")

        self.assertEqual(resp.status_code, 200, resp.data)
        item = next(
            i
            for i in resp.data["items"]
            if i["kind"] == "approval_pending" and i["document"] == doc.id
        )
        # Einreichungsdatum (heute), NICHT das 200 Tage alte Upload-Datum.
        self.assertEqual(item["date"], today.isoformat())

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
