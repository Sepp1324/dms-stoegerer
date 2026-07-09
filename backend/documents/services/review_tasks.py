"""Automatische Klärungsaufgaben für die Review-Inbox.

Die Pipeline erzeugt keine UI-Entscheidungen direkt. Stattdessen schreibt sie
konkrete, idempotente ``DocumentReviewTask``-Einträge: *warum* braucht dieses
Dokument menschliche Aufmerksamkeit? Dadurch bleibt ``Document.review_status``
der grobe Workflow-Status, während diese Schicht die praktische Mailroom-Arbeit
erklärbar macht.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from documents.models import (
    AuditLogEntry,
    CaseFileCandidate,
    Document,
    DocumentReviewTask,
    DocumentVersion,
    ExtractionCandidate,
    OCRStatus,
)


@dataclass(frozen=True)
class ReviewTaskSpec:
    kind: str
    signature: str
    priority: int
    message: str
    suggested_action: str = ""
    data: dict = field(default_factory=dict)


MANAGED_KINDS = {choice for choice, _label in DocumentReviewTask.Kind.choices}
MIN_USEFUL_OCR_CHARS = 20


def build_task_specs(document: Document) -> list[ReviewTaskSpec]:
    """Leitet den aktuellen Klärungsbedarf eines Dokuments deterministisch ab."""
    specs: list[ReviewTaskSpec] = []
    version = document.current_version

    missing = []
    if document.correspondent_id is None:
        missing.append("Korrespondent")
    if document.document_type_id is None:
        missing.append("Dokumenttyp")
    if document.storage_path_id is None:
        missing.append("Ablagepfad")
    if document.folder_id is None:
        missing.append("Ordner")

    if missing:
        specs.append(
            ReviewTaskSpec(
                kind=DocumentReviewTask.Kind.METADATA_MISSING,
                signature=f"metadata_missing:{','.join(sorted(missing))}",
                priority=20,
                message=f"{', '.join(missing)} fehlen.",
                suggested_action="Metadaten ergänzen oder passende Regel anlegen.",
                data={"missing": missing},
            )
        )

    if version is not None:
        if (
            version.processing_state == DocumentVersion.ProcessingState.FAILED
            or version.ocr_status == OCRStatus.FAILED
        ):
            specs.append(
                ReviewTaskSpec(
                    kind=DocumentReviewTask.Kind.OCR_FAILED,
                    signature=f"ocr_failed:v{version.id}",
                    priority=10,
                    message="OCR oder Verarbeitung ist fehlgeschlagen.",
                    suggested_action="OCR-Status öffnen und Verarbeitung erneut starten.",
                    data={
                        "version": version.id,
                        "processing_state": version.processing_state,
                        "processing_failed_step": version.processing_failed_step,
                        "ocr_status": version.ocr_status,
                        "ocr_error": version.ocr_error,
                    },
                )
            )
        elif (
            version.processing_state == DocumentVersion.ProcessingState.READY
            and len((version.ocr_text or "").strip()) < MIN_USEFUL_OCR_CHARS
        ):
            specs.append(
                ReviewTaskSpec(
                    kind=DocumentReviewTask.Kind.OCR_EMPTY,
                    signature=f"ocr_empty:v{version.id}",
                    priority=15,
                    message="OCR-Text ist leer oder sehr kurz.",
                    suggested_action="Vorschau prüfen, OCR wiederholen oder Scanqualität verbessern.",
                    data={
                        "version": version.id,
                        "chars": len((version.ocr_text or "").strip()),
                    },
                )
            )

        if version.sha256:
            duplicate_ids = list(
                DocumentVersion.objects.filter(sha256=version.sha256)
                .exclude(document_id=document.id)
                .values_list("document_id", flat=True)
                .distinct()[:5]
            )
            if duplicate_ids:
                specs.append(
                    ReviewTaskSpec(
                        kind=DocumentReviewTask.Kind.DUPLICATE_SUSPECTED,
                        signature=f"duplicate:{version.sha256[:16]}",
                        priority=25,
                        message="Diese Datei existiert vermutlich bereits im Archiv.",
                        suggested_action="Dubletten prüfen und ggf. zusammenführen.",
                        data={"document_ids": duplicate_ids, "sha256": version.sha256},
                    )
                )

    classification_rules = (document.classification or {}).get("rules") or []
    if missing and not classification_rules:
        specs.append(
            ReviewTaskSpec(
                kind=DocumentReviewTask.Kind.CLASSIFICATION_LOW_CONFIDENCE,
                signature="classification_low_confidence:no_rules",
                priority=45,
                message="Keine Klassifizierungsregel hat gegriffen.",
                suggested_action="Dokument prüfen und daraus bei Bedarf eine Regel lernen.",
                data={"classification": document.classification or {}},
            )
        )

    if document.ai_suggestions:
        specs.append(
            ReviewTaskSpec(
                kind=DocumentReviewTask.Kind.AI_SUGGESTION_PENDING,
                signature="ai_suggestion_pending",
                priority=35,
                message="KI-Metadatenvorschläge warten auf Prüfung.",
                suggested_action="Vorschläge übernehmen oder verwerfen.",
                data={"fields": sorted(document.ai_suggestions.keys())},
            )
        )

    extraction_count = document.extraction_candidates.filter(
        status=ExtractionCandidate.Status.PENDING
    ).count()
    if extraction_count:
        specs.append(
            ReviewTaskSpec(
                kind=DocumentReviewTask.Kind.EXTRACTION_PENDING,
                signature="extraction_pending",
                priority=30,
                message=f"{extraction_count} Strukturvorschlag wartet auf Prüfung.",
                suggested_action="Strukturdaten übernehmen oder verwerfen.",
                data={"count": extraction_count},
            )
        )

    case_count = document.case_file_candidates.filter(
        status=CaseFileCandidate.Status.PENDING
    ).count()
    if case_count:
        specs.append(
            ReviewTaskSpec(
                kind=DocumentReviewTask.Kind.CASE_FILE_PENDING,
                signature="case_file_pending",
                priority=32,
                message=f"{case_count} Aktenvorschlag wartet auf Prüfung.",
                suggested_action="Akte zuordnen oder Vorschlag verwerfen.",
                data={"count": case_count},
            )
        )

    if not document.asn:
        specs.append(
            ReviewTaskSpec(
                kind=DocumentReviewTask.Kind.ASN_MISSING,
                signature="asn_missing",
                priority=5,
                message="Dokument hat keine Archivnummer.",
                suggested_action="ASN-Backfill ausführen oder Dokument erneut speichern.",
            )
        )

    if (document.mail_subject or document.mail_sender) and missing:
        specs.append(
            ReviewTaskSpec(
                kind=DocumentReviewTask.Kind.EMAIL_NEEDS_REVIEW,
                signature="email_needs_review",
                priority=40,
                message="E-Mail-Import braucht fachliche Nacharbeit.",
                suggested_action="Absender, Betreff und Anhänge prüfen.",
                data={"subject": document.mail_subject, "sender": document.mail_sender},
            )
        )

    return specs


@transaction.atomic
def sync_document_review_tasks(document: Document) -> dict:
    """Erzeugt/aktualisiert offene Tasks und löst veraltete automatisch auf."""
    document = (
        Document.objects.select_related(
            "current_version",
            "correspondent",
            "document_type",
            "storage_path",
            "folder",
        )
        .prefetch_related("extraction_candidates", "case_file_candidates")
        .get(pk=document.pk)
    )
    specs = build_task_specs(document)
    desired = {spec.signature: spec for spec in specs}
    created = 0
    updated = 0

    for spec in specs:
        task = DocumentReviewTask.objects.filter(
            document=document, signature=spec.signature
        ).first()
        if task is None:
            DocumentReviewTask.objects.create(
                document=document,
                kind=spec.kind,
                signature=spec.signature,
                priority=spec.priority,
                message=spec.message,
                suggested_action=spec.suggested_action,
                data=spec.data,
            )
            created += 1
            continue

        if task.status != DocumentReviewTask.Status.OPEN:
            # Explizit erledigte/ignorierte Tasks bleiben ruhig. Neue Versionen
            # bekommen über version_id-basierte Signaturen eigene Tasks.
            continue

        changed_fields = []
        for field_name, value in (
            ("kind", spec.kind),
            ("priority", spec.priority),
            ("message", spec.message),
            ("suggested_action", spec.suggested_action),
            ("data", spec.data),
        ):
            if getattr(task, field_name) != value:
                setattr(task, field_name, value)
                changed_fields.append(field_name)
        if changed_fields:
            task.save(update_fields=[*changed_fields, "updated_at"])
            updated += 1

    obsolete = DocumentReviewTask.objects.filter(
        document=document,
        status=DocumentReviewTask.Status.OPEN,
        kind__in=MANAGED_KINDS,
    ).exclude(signature__in=desired.keys())
    resolved = obsolete.update(
        status=DocumentReviewTask.Status.RESOLVED,
        resolved_at=timezone.now(),
        resolved_by=None,
    )

    open_count = DocumentReviewTask.objects.filter(
        document=document, status=DocumentReviewTask.Status.OPEN
    ).count()
    if open_count and document.review_status == Document.ReviewStatus.REVIEWED:
        Document.objects.filter(pk=document.pk).update(
            review_status=Document.ReviewStatus.NEEDS_REVIEW
        )

    return {
        "created": created,
        "updated": updated,
        "resolved": resolved,
        "open": open_count,
    }


def resolve_review_tasks(
    document: Document,
    *,
    actor=None,
    task_ids: list[int] | None = None,
    target_status: str = DocumentReviewTask.Status.RESOLVED,
    reason: str = "",
) -> int:
    """Schließt offene Tasks für ein Dokument auditierbar ab."""
    if target_status not in {
        DocumentReviewTask.Status.RESOLVED,
        DocumentReviewTask.Status.IGNORED,
    }:
        raise ValueError(f"Ungültiger Zielstatus: {target_status!r}")

    qs = DocumentReviewTask.objects.filter(
        document=document,
        status=DocumentReviewTask.Status.OPEN,
    )
    if task_ids is not None:
        qs = qs.filter(id__in=task_ids)

    tasks = list(qs)
    if not tasks:
        return 0

    now = timezone.now()
    for task in tasks:
        task.status = target_status
        task.resolved_at = now
        task.resolved_by = actor
        task.save(update_fields=["status", "resolved_at", "resolved_by", "updated_at"])

    AuditLogEntry.objects.create(
        actor=actor,
        action=(
            "review_task_ignore"
            if target_status == DocumentReviewTask.Status.IGNORED
            else "review_task_resolve"
        ),
        object_type="Document",
        object_id=str(document.id),
        detail={
            "task_ids": [task.id for task in tasks],
            "target_status": target_status,
            "reason": reason,
        },
    )
    return len(tasks)
