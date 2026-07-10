"""Zentrales Fristen-/Timeline-Aggregat für das DMS.

Das Fristen-Center bündelt bewusst mehrere fachliche Quellen: Wiedervorlagen,
Vertragsfristen, Review-Aufgaben, Freigaben und Aufbewahrung. Der Service bleibt
zustandslos und deterministisch; Owner-Isolation passiert über das übergebene
Dokumenten-Queryset aus dem View.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from documents.models import ContractRecord, Document, DocumentReminder, DocumentReviewTask


TimelineBucket = str


@dataclass(frozen=True)
class TimelineItem:
    source: str
    source_id: int
    kind: str
    title: str
    description: str
    date: date
    document_id: int
    document_title: str
    severity: str
    action_label: str
    metadata: dict[str, Any]

    def as_dict(self, *, today: date) -> dict[str, Any]:
        delta = (self.date - today).days
        return {
            "id": f"{self.source}:{self.source_id}:{self.kind}",
            "source": self.source,
            "source_id": self.source_id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "date": self.date.isoformat(),
            "days_delta": delta,
            "bucket": bucket_for(self.date, today=today),
            "severity": self.severity,
            "document": self.document_id,
            "document_title": self.document_title,
            "action_label": self.action_label,
            "metadata": self.metadata,
        }


def build_timeline(
    visible_documents: QuerySet[Document],
    *,
    days: int = 30,
) -> dict[str, Any]:
    today = timezone.localdate()
    days = max(0, min(days, 365))
    horizon = today + timedelta(days=days)

    document_qs = visible_documents.select_related(
        "correspondent",
        "document_type",
        "folder",
        "case_file",
        "current_version",
    )
    items = [
        *_reminder_items(document_qs, today=today, horizon=horizon),
        *_contract_items(document_qs, today=today, horizon=horizon),
        *_review_task_items(document_qs, today=today, horizon=horizon),
        *_approval_items(document_qs, today=today, horizon=horizon),
        *_retention_items(document_qs, today=today, horizon=horizon),
    ]
    serialized = [
        item.as_dict(today=today)
        for item in sorted(
            items,
            key=lambda item: (item.date, severity_rank(item.severity), item.source, item.source_id),
        )
    ]
    buckets: dict[TimelineBucket, list[dict[str, Any]]] = {
        "overdue": [],
        "today": [],
        "soon": [],
        "upcoming": [],
    }
    for item in serialized:
        buckets[item["bucket"]].append(item)

    return {
        "generated_at": timezone.now().isoformat(),
        "today": today.isoformat(),
        "horizon": horizon.isoformat(),
        "days": days,
        "summary": _summary(serialized),
        "buckets": buckets,
        "items": serialized,
    }


def build_ics(
    visible_documents: QuerySet[Document],
    *,
    days: int = 90,
    calendar_name: str = "DMS Fristen",
) -> str:
    timeline = build_timeline(visible_documents, days=days)
    generated = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//dms-stoegerer//timeline//DE",
        f"X-WR-CALNAME:{_ics_escape(calendar_name)}",
    ]
    for item in timeline["items"]:
        date_value = item["date"].replace("-", "")
        uid = f"{item['id']}@dms-stoegerer"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{_ics_escape(uid)}",
                f"DTSTAMP:{generated}",
                f"DTSTART;VALUE=DATE:{date_value}",
                f"SUMMARY:{_ics_escape(item['title'])}",
                f"DESCRIPTION:{_ics_escape(item['description'] + ' · ' + item['document_title'])}",
                f"CATEGORIES:{_ics_escape(item['source'] + ',' + item['severity'])}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def bucket_for(value: date, *, today: date) -> TimelineBucket:
    if value < today:
        return "overdue"
    if value == today:
        return "today"
    if value <= today + timedelta(days=7):
        return "soon"
    return "upcoming"


def severity_rank(severity: str) -> int:
    return {"high": 0, "medium": 1, "low": 2, "info": 3}.get(severity, 9)


def _reminder_items(
    documents: QuerySet[Document],
    *,
    today: date,
    horizon: date,
) -> list[TimelineItem]:
    qs = (
        DocumentReminder.objects.select_related("document")
        .filter(document__in=documents, done=False, remind_on__lte=horizon)
        .order_by("remind_on", "id")
    )
    return [
        TimelineItem(
            source="reminder",
            source_id=reminder.id,
            kind="reminder_due",
            title="Wiedervorlage",
            description=reminder.note or "Offene Wiedervorlage prüfen.",
            date=reminder.remind_on,
            document_id=reminder.document_id,
            document_title=reminder.document.title,
            severity="high" if reminder.remind_on < today else "medium",
            action_label="Dokument öffnen",
            metadata={"done": reminder.done},
        )
        for reminder in qs
    ]


def _contract_items(
    documents: QuerySet[Document],
    *,
    today: date,
    horizon: date,
) -> list[TimelineItem]:
    qs = (
        ContractRecord.objects.select_related("document")
        .filter(document__in=documents, status=ContractRecord.Status.ACTIVE)
        .filter(cancel_until__lte=horizon)
        .order_by("cancel_until", "id")
    )
    items = [
        TimelineItem(
            source="contract",
            source_id=contract.id,
            kind="contract_cancel_until",
            title="Kündigungsfrist",
            description=_contract_description(contract, "Kündigen bis"),
            date=contract.cancel_until,
            document_id=contract.document_id,
            document_title=contract.document.title,
            severity="high" if contract.cancel_until and contract.cancel_until <= today + timedelta(days=14) else "medium",
            action_label="Vertrag öffnen",
            metadata={"provider": contract.provider, "contract_number": contract.contract_number},
        )
        for contract in qs
        if contract.cancel_until is not None
    ]

    due_qs = (
        ContractRecord.objects.select_related("document")
        .filter(document__in=documents, status=ContractRecord.Status.ACTIVE)
        .filter(next_due_on__lte=horizon)
        .order_by("next_due_on", "id")
    )
    items.extend(
        TimelineItem(
            source="contract",
            source_id=contract.id,
            kind="contract_next_due",
            title="Vertragsfälligkeit",
            description=_contract_description(contract, "Fällig"),
            date=contract.next_due_on,
            document_id=contract.document_id,
            document_title=contract.document.title,
            severity="medium" if contract.next_due_on and contract.next_due_on <= today + timedelta(days=14) else "low",
            action_label="Vertrag öffnen",
            metadata={"provider": contract.provider, "amount": str(contract.amount) if contract.amount is not None else None},
        )
        for contract in due_qs
        if contract.next_due_on is not None
    )
    return items


def _review_task_items(
    documents: QuerySet[Document],
    *,
    today: date,
    horizon: date,
) -> list[TimelineItem]:
    lower_bound = today - timedelta(days=365)
    qs = (
        DocumentReviewTask.objects.select_related("document")
        .filter(document__in=documents, status=DocumentReviewTask.Status.OPEN)
        .filter(created_at__date__gte=lower_bound, created_at__date__lte=horizon)
        .order_by("priority", "created_at", "id")
    )
    items = []
    for task in qs:
        task_date = timezone.localtime(task.created_at).date()
        items.append(
            TimelineItem(
                source="review_task",
                source_id=task.id,
                kind=f"review_{task.kind}",
                title=task.get_kind_display(),
                description=task.message,
                date=task_date,
                document_id=task.document_id,
                document_title=task.document.title,
                severity="high" if task.priority <= 20 else "medium",
                action_label=task.suggested_action or "Inbox öffnen",
                metadata={"priority": task.priority, "suggested_action": task.suggested_action},
            )
        )
    return items


def _approval_items(
    documents: QuerySet[Document],
    *,
    today: date,
    horizon: date,
) -> list[TimelineItem]:
    lower_bound = today - timedelta(days=365)
    qs = (
        documents.filter(
            status=Document.ApprovalStatus.ZUR_FREIGABE,
            added_at__date__gte=lower_bound,
            added_at__date__lte=horizon,
        )
        .order_by("added_at", "id")
    )
    return [
        TimelineItem(
            source="approval",
            source_id=document.id,
            kind="approval_pending",
            title="Freigabe offen",
            description="Dokument wartet auf Freigabe.",
            date=timezone.localtime(document.added_at).date(),
            document_id=document.id,
            document_title=document.title,
            severity="medium",
            action_label="Freigabe prüfen",
            metadata={"status": document.status},
        )
        for document in qs
    ]


def _retention_items(
    documents: QuerySet[Document],
    *,
    today: date,
    horizon: date,
) -> list[TimelineItem]:
    qs = (
        documents.filter(retention_until__isnull=False, retention_until__lte=horizon)
        .order_by("retention_until", "id")
    )
    return [
        TimelineItem(
            source="retention",
            source_id=document.id,
            kind="retention_until",
            title="Aufbewahrungsfrist",
            description=(
                "Aufbewahrung abgelaufen."
                if document.retention_until and document.retention_until < today
                else "Aufbewahrung läuft bald ab."
            ),
            date=document.retention_until,
            document_id=document.id,
            document_title=document.title,
            severity="high" if document.retention_until and document.retention_until < today else "medium",
            action_label="Archiv prüfen",
            metadata={"legal_hold": document.legal_hold},
        )
        for document in qs
        if document.retention_until is not None
    ]


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for item in items:
        by_source[item["source"]] = by_source.get(item["source"], 0) + 1
        by_severity[item["severity"]] = by_severity.get(item["severity"], 0) + 1
    return {
        "total": len(items),
        "overdue": sum(1 for item in items if item["bucket"] == "overdue"),
        "today": sum(1 for item in items if item["bucket"] == "today"),
        "soon": sum(1 for item in items if item["bucket"] == "soon"),
        "upcoming": sum(1 for item in items if item["bucket"] == "upcoming"),
        "high": by_severity.get("high", 0),
        "medium": by_severity.get("medium", 0),
        "low": by_severity.get("low", 0),
        "by_source": by_source,
    }


def _contract_description(contract: ContractRecord, prefix: str) -> str:
    provider = contract.provider or (
        contract.document.correspondent.name
        if contract.document and contract.document.correspondent_id
        else "Vertrag"
    )
    number = f" · {contract.contract_number}" if contract.contract_number else ""
    return f"{prefix}: {provider}{number}"


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )
