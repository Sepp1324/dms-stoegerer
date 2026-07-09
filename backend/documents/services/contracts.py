"""Heuristische Vertrags- und Fristenerkennung.

Contract Center v1 ist bewusst deterministisch: Es extrahiert nur klar
erkennbare Signale aus OCR/Text/Metadaten, erzeugt daraus einen
``ContractRecord`` und legt Wiedervorlagen für Kündigungs-/Fälligkeitsdaten an.
Unsichere Treffer landen in der Review-Inbox statt still als Wahrheit zu gelten.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from documents.models import (
    AuditLogEntry,
    ContractRecord,
    Document,
    DocumentReminder,
    DocumentReviewTask,
)


MIN_CONTRACT_CONFIDENCE = 35
REVIEW_CONFIDENCE = 80
CONTRACT_REVIEW_SIGNATURE = "contract_review"


@dataclass
class ContractExtraction:
    fields: dict
    confidence: int
    signals: list[str]


def extract_contract_data(document: Document) -> ContractExtraction | None:
    """Extrahiert Vertragsdaten aus Dokumentmetadaten + aktuellem OCR-Text."""
    version = document.current_version
    text = _normalize_text(
        " ".join(
            [
                document.title or "",
                document.correspondent.name if document.correspondent_id else "",
                document.document_type.name if document.document_type_id else "",
                version.ocr_text if version else "",
            ]
        )
    )
    if not text.strip():
        return None

    signals: list[str] = []
    fields: dict = {}

    contract_type = _detect_contract_type(text)
    if contract_type:
        fields["contract_type"] = contract_type
        signals.append("contract_type")

    provider = document.correspondent.name if document.correspondent_id else ""
    if provider:
        fields["provider"] = provider
        signals.append("provider")

    contract_number = _extract_contract_number(text)
    if contract_number:
        fields["contract_number"] = contract_number
        signals.append("contract_number")

    amount = _extract_amount(text)
    if amount is not None:
        fields["amount"] = amount
        fields["currency"] = "EUR"
        signals.append("amount")

    billing_cycle = _detect_billing_cycle(text)
    if billing_cycle:
        fields["billing_cycle"] = billing_cycle
        signals.append("billing_cycle")

    starts_on = _date_after(text, ("vertragsbeginn", "beginn", "startet am"))
    if starts_on:
        fields["starts_on"] = starts_on
        signals.append("starts_on")

    ends_on = _date_after(
        text,
        ("vertragsende", "endet am", "laufzeit bis", "ende der laufzeit"),
    )
    if ends_on:
        fields["ends_on"] = ends_on
        signals.append("ends_on")

    notice_days = _extract_notice_period_days(text)
    if notice_days:
        fields["notice_period_days"] = notice_days
        signals.append("notice_period")

    cancel_until = _date_after(
        text,
        ("kündigen bis", "kuendigen bis", "kündigung bis", "kuendigung bis"),
    )
    if cancel_until is None and ends_on and notice_days:
        cancel_until = ends_on - timedelta(days=notice_days)
    if cancel_until:
        fields["cancel_until"] = cancel_until
        signals.append("cancel_until")

    next_due_on = _date_after(
        text,
        ("nächste fälligkeit", "naechste faelligkeit", "fälligkeit", "faelligkeit"),
    )
    if next_due_on:
        fields["next_due_on"] = next_due_on
        signals.append("next_due_on")

    if not _looks_contract_like(text, signals):
        return None

    confidence = _confidence(signals, fields)
    if confidence < MIN_CONTRACT_CONFIDENCE:
        return None

    fields.setdefault("contract_type", ContractRecord.ContractType.OTHER)
    fields.setdefault("billing_cycle", ContractRecord.BillingCycle.UNKNOWN)
    fields["status"] = _status(fields)
    fields["confidence"] = confidence
    fields["needs_review"] = _needs_review(fields, confidence)
    fields["source"] = ContractRecord.Source.HEURISTIC
    fields["extracted_from_version"] = version
    fields["case_file"] = document.case_file
    return ContractExtraction(fields=fields, confidence=confidence, signals=signals)


@transaction.atomic
def sync_contract_record(document: Document, *, actor=None) -> dict:
    """Erzeugt/aktualisiert einen ContractRecord für ein Dokument, falls erkennbar."""
    document = (
        Document.objects.select_related(
            "current_version",
            "correspondent",
            "document_type",
            "case_file",
            "contract_record",
        )
        .filter(pk=document.pk)
        .first()
    )
    if document is None:
        return {"status": "missing"}

    extracted = extract_contract_data(document)
    if extracted is None:
        return {"status": "no_contract", "document_id": document.id}

    try:
        record = document.contract_record
    except ContractRecord.DoesNotExist:
        record = None
    created = record is None
    if created:
        record = ContractRecord(document=document)

    manual_confirmed = (
        not created
        and record.source == ContractRecord.Source.MANUAL
        and record.reviewed_at is not None
        and not record.needs_review
    )
    fields = dict(extracted.fields)
    if manual_confirmed:
        fields["needs_review"] = False
        fields["source"] = ContractRecord.Source.MANUAL

    changed_fields = []
    for field_name, value in fields.items():
        if getattr(record, field_name) != value:
            setattr(record, field_name, value)
            changed_fields.append(field_name)

    if created:
        record.save()
    elif changed_fields:
        record.save(update_fields=[*changed_fields, "updated_at"])

    reminders = ensure_contract_reminders(record)
    sync_contract_review_task(record, actor=actor)

    AuditLogEntry.objects.create(
        actor=actor,
        action="contract_detected" if created else "contract_updated",
        object_type="ContractRecord",
        object_id=str(record.id),
        detail={
            "document": document.id,
            "confidence": record.confidence,
            "signals": extracted.signals,
            "created": created,
            "changed_fields": changed_fields,
            "reminders": reminders,
        },
    )
    return {
        "status": "created" if created else "updated" if changed_fields else "unchanged",
        "contract_id": record.id,
        "document_id": document.id,
        "confidence": record.confidence,
        "needs_review": record.needs_review,
        "reminders": reminders,
    }


def ensure_contract_reminders(record: ContractRecord) -> int:
    """Legt Wiedervorlagen für Kündigungsfrist und nächste Fälligkeit an."""
    created = 0
    today = timezone.now().date()
    provider = record.provider or record.document.title
    reminders = [
        (
            record.cancel_until,
            f"[Contract Center] Kündigungsfrist prüfen: {provider}",
        ),
        (
            record.next_due_on,
            f"[Contract Center] Nächste Fälligkeit prüfen: {provider}",
        ),
    ]
    for remind_on, note in reminders:
        if remind_on is None or remind_on < today:
            continue
        _, was_created = DocumentReminder.objects.get_or_create(
            document=record.document,
            remind_on=remind_on,
            note=note,
            defaults={"created_by": record.document.owner},
        )
        if was_created:
            created += 1
    return created


def confirm_contract(record: ContractRecord, *, actor=None) -> ContractRecord:
    """Markiert extrahierte Vertragsdaten als fachlich geprüft."""
    record.needs_review = False
    record.reviewed_at = timezone.now()
    record.reviewed_by = actor
    if record.status == ContractRecord.Status.UNCLEAR:
        record.status = _status(
            {"ends_on": record.ends_on, "confidence": record.confidence}
        )
    record.source = ContractRecord.Source.MANUAL
    record.save(
        update_fields=[
            "needs_review",
            "reviewed_at",
            "reviewed_by",
            "status",
            "source",
            "updated_at",
        ]
    )
    sync_contract_review_task(record, actor=actor)
    AuditLogEntry.objects.create(
        actor=actor,
        action="contract_confirmed",
        object_type="ContractRecord",
        object_id=str(record.id),
        detail={"document": record.document_id},
    )
    return record


def sync_contract_review_task(record: ContractRecord, *, actor=None) -> None:
    """Synchronisiert die fachliche Review-Aufgabe für einen Vertrag.

    Geschlossene Aufgaben werden nicht durch einen erneuten Scan wieder geöffnet:
    Wenn jemand einen Hinweis bewusst ignoriert oder erledigt hat, respektiert der
    Scanner diese Entscheidung. Nur offene Aufgaben werden aktualisiert.
    """
    open_tasks = DocumentReviewTask.objects.filter(
        document=record.document,
        status=DocumentReviewTask.Status.OPEN,
        kind=DocumentReviewTask.Kind.CONTRACT_REVIEW,
        signature=CONTRACT_REVIEW_SIGNATURE,
    )
    if not record.needs_review:
        open_tasks.update(
            status=DocumentReviewTask.Status.RESOLVED,
            resolved_at=timezone.now(),
            resolved_by=actor,
        )
        return

    message = "Vertragsdaten bitte prüfen."
    if record.cancel_until:
        message = f"Kündigungsfrist bis {record.cancel_until:%d.%m.%Y} prüfen."
    elif record.next_due_on:
        message = f"Nächste Fälligkeit am {record.next_due_on:%d.%m.%Y} prüfen."
    elif record.contract_number:
        message = f"Vertrag {record.contract_number} prüfen."

    existing = open_tasks.first()
    defaults = {
        "priority": 28 if record.cancel_until else 38,
        "message": message,
        "suggested_action": "Vertragsdaten bestätigen oder im Contract Center korrigieren.",
        "data": {"contract": record.id, "confidence": record.confidence},
    }
    if existing is not None:
        for field_name, value in defaults.items():
            setattr(existing, field_name, value)
        existing.save(update_fields=[*defaults.keys(), "updated_at"])
        return

    closed_exists = DocumentReviewTask.objects.filter(
        document=record.document,
        kind=DocumentReviewTask.Kind.CONTRACT_REVIEW,
        signature=CONTRACT_REVIEW_SIGNATURE,
    ).exists()
    if closed_exists:
        return

    DocumentReviewTask.objects.create(
        document=record.document,
        signature=CONTRACT_REVIEW_SIGNATURE,
        kind=DocumentReviewTask.Kind.CONTRACT_REVIEW,
        status=DocumentReviewTask.Status.OPEN,
        **defaults,
    )


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _looks_contract_like(text: str, signals: list[str]) -> bool:
    if {"contract_number", "cancel_until", "notice_period"} & set(signals):
        return True
    keywords = (
        "vertrag",
        "polizze",
        "versicherung",
        "kündigung",
        "kuendigung",
        "laufzeit",
        "monatlich",
        "jährlich",
        "jaehrlich",
        "abo",
        "miete",
        "kredit",
    )
    return any(keyword in text.lower() for keyword in keywords) and len(signals) >= 2


def _detect_contract_type(text: str) -> str | None:
    lower = text.lower()
    rules = [
        (ContractRecord.ContractType.INSURANCE, ("versicherung", "polizze", "police")),
        (ContractRecord.ContractType.ENERGY, ("strom", "gas", "energie", "netz")),
        (ContractRecord.ContractType.TELECOM, ("mobilfunk", "handy", "internet", "telefon")),
        (ContractRecord.ContractType.RENT, ("mietvertrag", "miete", "vermieter")),
        (ContractRecord.ContractType.LOAN, ("kredit", "darlehen", "finanzierung")),
        (ContractRecord.ContractType.SUBSCRIPTION, ("abo", "mitgliedschaft", "subscription")),
        (ContractRecord.ContractType.PUBLIC, ("bescheid", "finanzamt", "behörde")),
    ]
    for kind, keywords in rules:
        if any(keyword in lower for keyword in keywords):
            return kind
    if "vertrag" in lower:
        return ContractRecord.ContractType.OTHER
    return None


def _detect_billing_cycle(text: str) -> str | None:
    lower = text.lower()
    if any(word in lower for word in ("monatlich", "pro monat", " mtl")):
        return ContractRecord.BillingCycle.MONTHLY
    if any(word in lower for word in ("vierteljährlich", "vierteljaehrlich", "quartal")):
        return ContractRecord.BillingCycle.QUARTERLY
    if any(word in lower for word in ("jährlich", "jaehrlich", "pro jahr", "p.a.")):
        return ContractRecord.BillingCycle.YEARLY
    if "einmalig" in lower:
        return ContractRecord.BillingCycle.ONE_TIME
    return None


def _extract_contract_number(text: str) -> str:
    pattern = re.compile(
        r"(?i)(?:vertrags(?:nummer|nr\.?)|polizz(?:en)?nummer|police(?:nummer)?|"
        r"kundennummer|mandatsreferenz)\s*[:#]?\s*([A-Z0-9][A-Z0-9/\-.]{3,})"
    )
    match = pattern.search(text)
    return _clean_identifier(match.group(1)) if match else ""


def _extract_amount(text: str) -> Decimal | None:
    patterns = [
        r"(?i)(?:betrag|beitrag|prämie|praemie|rate|zahlung).{0,40}?([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2})",
        r"(?i)(?:eur|€)\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2})",
        r"(?i)([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2})\s*(?:eur|€)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        raw = match.group(1).replace(".", "").replace(",", ".")
        try:
            return Decimal(raw).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            continue
    return None


def _date_after(text: str, labels: tuple[str, ...]):
    lower = text.lower()
    for label in labels:
        pos = lower.find(label)
        if pos < 0:
            continue
        match = re.search(
            r"(\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2})",
            text[pos : pos + 140],
        )
        if match:
            parsed = _parse_date(match.group(1))
            if parsed:
                return parsed
    return None


def _parse_date(raw: str):
    from datetime import date

    raw = raw.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    match = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", raw)
    if not match:
        return None
    day, month, year = [int(part) for part in match.groups()]
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_notice_period_days(text: str) -> int | None:
    lower = text.lower()
    pos = lower.find("kündigungsfrist")
    if pos < 0:
        pos = lower.find("kuendigungsfrist")
    if pos < 0:
        return None
    match = re.search(r"(\d{1,2})\s*(tag|tage|woche|wochen|monat|monate)", lower[pos : pos + 120])
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("tag"):
        return value
    if unit.startswith("woche"):
        return value * 7
    return value * 30


def _status(fields: dict) -> str:
    ends_on = fields.get("ends_on")
    if ends_on and ends_on < timezone.now().date():
        return ContractRecord.Status.EXPIRED
    if fields.get("confidence", 0) >= 50 or fields.get("contract_number"):
        return ContractRecord.Status.ACTIVE
    return ContractRecord.Status.UNCLEAR


def _needs_review(fields: dict, confidence: int) -> bool:
    return (
        confidence < REVIEW_CONFIDENCE
        or not fields.get("contract_number")
        or not (fields.get("cancel_until") or fields.get("next_due_on"))
    )


def _confidence(signals: list[str], fields: dict) -> int:
    weights = {
        "contract_number": 30,
        "contract_type": 15,
        "provider": 10,
        "amount": 12,
        "billing_cycle": 8,
        "starts_on": 8,
        "ends_on": 10,
        "notice_period": 12,
        "cancel_until": 18,
        "next_due_on": 12,
    }
    score = sum(weights.get(signal, 0) for signal in set(signals))
    if fields.get("contract_type") == ContractRecord.ContractType.OTHER:
        score -= 5
    return max(0, min(95, score))


def _clean_identifier(raw: str) -> str:
    return re.sub(r"[^A-Z0-9/\-.]", "", raw.upper()).strip(".-/")[:128]
