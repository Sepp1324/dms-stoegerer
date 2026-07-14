"""Fixkosten-/Ausgabenüberblick aus den strukturierten Vertragsdaten.

Aggregiert die aktiven Verträge (``ContractRecord``) zu einer Haushalts-Sicht:
Beträge werden anhand des Abrechnungsturnus auf einen **monatlichen** Wert
normalisiert (Quartal ÷3, Jahr ÷12), summiert und nach Kategorie/Anbieter
aufgeschlüsselt. Beträge in unterschiedlichen Währungen werden NICHT vermischt,
sondern je Währung getrennt ausgewiesen. Verträge ohne Betrag oder mit unklarem/
einmaligem Turnus fließen nicht in die laufenden Summen ein, werden aber als
Abdeckungs-Kennzahl gezählt (damit sichtbar ist, wie vollständig das Bild ist).
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from documents.models import ContractRecord

# Faktor: Betrag × Faktor = monatlicher Anteil.
_MONTHLY_FACTOR = {
    ContractRecord.BillingCycle.MONTHLY: Decimal(1),
    ContractRecord.BillingCycle.QUARTERLY: Decimal(1) / Decimal(3),
    ContractRecord.BillingCycle.YEARLY: Decimal(1) / Decimal(12),
}


def _q2(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def cost_overview(contract_qs, *, upcoming_days: int = 60, max_providers: int = 10) -> dict:
    """Baut den Fixkosten-Überblick aus einem (owner-gescopten) Vertrags-Queryset."""
    today = timezone.now().date()
    horizon = today + timedelta(days=upcoming_days)

    active = list(
        contract_qs.filter(status=ContractRecord.Status.ACTIVE).select_related("document")
    )

    coverage = {
        "active": len(active),
        "with_amount": 0,
        "recurring": 0,   # in die Summen eingerechnet
        "one_time": 0,
        "unknown": 0,     # Betrag fehlt ODER Turnus nicht normalisierbar
    }

    currency_monthly: dict[str, Decimal] = {}
    currency_count: dict[str, int] = {}
    by_type: dict[tuple[str, str], dict] = {}
    by_provider: dict[tuple[str, str], dict] = {}

    for contract in active:
        if contract.amount is None:
            coverage["unknown"] += 1
            continue
        coverage["with_amount"] += 1
        factor = _MONTHLY_FACTOR.get(contract.billing_cycle)
        if factor is None:
            if contract.billing_cycle == ContractRecord.BillingCycle.ONE_TIME:
                coverage["one_time"] += 1
            else:
                coverage["unknown"] += 1
            continue

        coverage["recurring"] += 1
        currency = contract.currency or "EUR"
        monthly = contract.amount * factor

        currency_monthly[currency] = currency_monthly.get(currency, Decimal(0)) + monthly
        currency_count[currency] = currency_count.get(currency, 0) + 1

        tkey = (contract.contract_type, currency)
        entry = by_type.setdefault(
            tkey,
            {
                "type": contract.contract_type,
                "label": contract.get_contract_type_display(),
                "currency": currency,
                "_monthly": Decimal(0),
                "count": 0,
            },
        )
        entry["_monthly"] += monthly
        entry["count"] += 1

        provider = contract.provider.strip() or "Ohne Anbieter"
        pkey = (provider, currency)
        pentry = by_provider.setdefault(
            pkey,
            {"provider": provider, "currency": currency, "_monthly": Decimal(0), "count": 0},
        )
        pentry["_monthly"] += monthly
        pentry["count"] += 1

    currency_totals = [
        {
            "currency": currency,
            "monthly": _q2(monthly),
            "yearly": _q2(monthly * 12),
            "contracts": currency_count[currency],
        }
        for currency, monthly in sorted(
            currency_monthly.items(), key=lambda kv: kv[1], reverse=True
        )
    ]

    def _finish(entries: list[dict]) -> list[dict]:
        out = []
        for entry in sorted(entries, key=lambda e: e["_monthly"], reverse=True):
            monthly = entry.pop("_monthly")
            entry["monthly"] = _q2(monthly)
            entry["yearly"] = _q2(monthly * 12)
            out.append(entry)
        return out

    by_type_out = _finish(list(by_type.values()))
    by_provider_out = _finish(list(by_provider.values()))[:max_providers]

    upcoming = [
        {
            "document": contract.document_id,
            "provider": contract.provider or None,
            "type_label": contract.get_contract_type_display(),
            "amount": float(contract.amount) if contract.amount is not None else None,
            "currency": contract.currency or "EUR",
            "cycle_label": contract.get_billing_cycle_display(),
            "due_on": contract.next_due_on.isoformat(),
            "document_title": contract.document.title,
        }
        for contract in sorted(
            (
                c
                for c in active
                if c.next_due_on is not None and today <= c.next_due_on <= horizon
            ),
            key=lambda c: c.next_due_on,
        )[:20]
    ]

    return {
        "currency_totals": currency_totals,
        "by_type": by_type_out,
        "by_provider": by_provider_out,
        "upcoming": upcoming,
        "coverage": coverage,
        "upcoming_days": upcoming_days,
        "generated_at": timezone.now().isoformat(),
    }
