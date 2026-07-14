"""Tests für den Fixkosten-/Ausgabenüberblick (spending.cost_overview)."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import ContractRecord, Document
from .services import spending

User = get_user_model()


def make_contract(owner, *, amount, cycle, ctype=ContractRecord.ContractType.OTHER,
                  currency="EUR", status=ContractRecord.Status.ACTIVE, provider="Anbieter",
                  next_due=None):
    doc = Document.objects.create(title=f"Vertrag {amount}/{cycle}", owner=owner)
    return ContractRecord.objects.create(
        document=doc,
        amount=amount,
        billing_cycle=cycle,
        contract_type=ctype,
        currency=currency,
        status=status,
        provider=provider,
        next_due_on=next_due,
    )


class CostOverviewServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="cost-u", password="pw", role="user")
        C = ContractRecord.BillingCycle
        make_contract(cls.user, amount=Decimal("30"), cycle=C.MONTHLY)          # 30/mo
        make_contract(cls.user, amount=Decimal("120"), cycle=C.YEARLY)          # 10/mo
        make_contract(cls.user, amount=Decimal("30"), cycle=C.QUARTERLY)        # 10/mo
        make_contract(cls.user, amount=None, cycle=C.MONTHLY)                   # unknown (kein Betrag)
        make_contract(cls.user, amount=Decimal("500"), cycle=C.ONE_TIME)       # einmalig

    def test_normalizes_and_sums_monthly_yearly(self):
        result = spending.cost_overview(
            ContractRecord.objects.filter(document__owner=self.user)
        )

        self.assertEqual(len(result["currency_totals"]), 1)
        eur = result["currency_totals"][0]
        self.assertEqual(eur["currency"], "EUR")
        self.assertEqual(eur["monthly"], 50.0)   # 30 + 10 + 10
        self.assertEqual(eur["yearly"], 600.0)
        self.assertEqual(eur["contracts"], 3)

    def test_coverage_counts(self):
        result = spending.cost_overview(
            ContractRecord.objects.filter(document__owner=self.user)
        )
        cov = result["coverage"]
        self.assertEqual(cov["active"], 5)
        self.assertEqual(cov["with_amount"], 4)
        self.assertEqual(cov["recurring"], 3)
        self.assertEqual(cov["one_time"], 1)
        self.assertEqual(cov["unknown"], 1)

    def test_upcoming_lists_due_within_horizon(self):
        soon = timezone.now().date() + timedelta(days=10)
        contract = make_contract(
            self.user, amount=Decimal("20"), cycle=ContractRecord.BillingCycle.MONTHLY,
            next_due=soon,
        )

        result = spending.cost_overview(
            ContractRecord.objects.filter(document__owner=self.user), upcoming_days=30
        )

        due_docs = [u["document"] for u in result["upcoming"]]
        self.assertIn(contract.document_id, due_docs)

    def test_ignores_non_active(self):
        make_contract(
            self.user, amount=Decimal("999"), cycle=ContractRecord.BillingCycle.MONTHLY,
            status=ContractRecord.Status.CANCELED,
        )
        result = spending.cost_overview(
            ContractRecord.objects.filter(document__owner=self.user)
        )
        # gekündigter Vertrag zählt nicht in die 50/mo
        self.assertEqual(result["currency_totals"][0]["monthly"], 50.0)


class CostOverviewApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="cost-api", password="pw", role="user")
        cls.other = User.objects.create_user(username="cost-other", password="pw", role="user")
        make_contract(cls.user, amount=Decimal("40"), cycle=ContractRecord.BillingCycle.MONTHLY)
        make_contract(cls.other, amount=Decimal("999"), cycle=ContractRecord.BillingCycle.MONTHLY)

    def test_endpoint_is_owner_scoped(self):
        self.client.force_authenticate(self.user)

        resp = self.client.get("/api/contracts/cost-overview/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["currency_totals"]), 1)
        self.assertEqual(resp.data["currency_totals"][0]["monthly"], 40.0)
