"""Services für gespeicherte Dokumentansichten."""

from decimal import Decimal, InvalidOperation
import re

from django.db.models import Case, DecimalField, Q, Value, When
from django.db.models.functions import Cast

from documents.models import CustomFieldValue, Document, DocumentVersion
from documents.services import asn as asn_service

_CUSTOM_FIELD_PARAM_RE = re.compile(r"^custom_field_(\d+)_(gte|lte)$")
_ASN_QUERY_RE = re.compile(r"(?i)^\s*(?:asn)?\s*[0-9]+\s*$")
_NUMERIC_VALUE_RE = r"^-?[0-9]+(\.[0-9]+)?$"
_DECIMAL_OUTPUT = DecimalField(max_digits=30, decimal_places=10)


def _visible_documents_for(user):
    qs = Document.objects.select_related(
        "correspondent",
        "document_type",
        "folder",
        "case_file",
        "current_version",
    ).prefetch_related("tags")
    if not getattr(user, "is_dms_admin", False):
        qs = qs.filter(owner=user)
    return qs


def _apply_custom_field_filters(qs, custom_filters):
    if not isinstance(custom_filters, dict):
        return qs

    for key, raw_value in custom_filters.items():
        match = _CUSTOM_FIELD_PARAM_RE.match(str(key))
        if not match:
            continue
        field_id, op = int(match.group(1)), match.group(2)
        try:
            bound = Decimal(raw_value)
        except (InvalidOperation, TypeError, ValueError):
            continue

        numeric = Case(
            When(value__regex=_NUMERIC_VALUE_RE, then=Cast("value", _DECIMAL_OUTPUT)),
            default=Value(None),
            output_field=_DECIMAL_OUTPUT,
        )
        lookup = "num__gte" if op == "gte" else "num__lte"
        matching = (
            CustomFieldValue.objects.filter(field_id=field_id)
            .annotate(num=numeric)
            .filter(**{lookup: bound})
            .values("document_id")
        )
        qs = qs.filter(id__in=matching)
    return qs


def filter_documents_for_query(user, query):
    """Wendet eine gespeicherte Dokumentlisten-Query owner-sicher an."""

    query = query if isinstance(query, dict) else {}
    qs = _visible_documents_for(user)

    q = str(query.get("q") or "").strip()
    if q and _ASN_QUERY_RE.match(q):
        asn_value = asn_service.coerce_asn(q)
        if asn_value is not None:
            asn_qs = qs.filter(asn=asn_value)
            if asn_qs.exists():
                return asn_qs.distinct()

    if 0 < len(q) < 3:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(correspondent__name__icontains=q)
            | Q(document_type__name__icontains=q)
            | Q(tags__name__icontains=q)
            | Q(mail_subject__icontains=q)
            | Q(mail_sender__icontains=q)
            | Q(current_version__ocr_text__icontains=q)
        )
    elif q:
        from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

        vector = (
            SearchVector("title", weight="A", config="german")
            + SearchVector("correspondent__name", weight="A", config="german")
            + SearchVector("document_type__name", weight="B", config="german")
            + SearchVector("tags__name", weight="B", config="german")
            + SearchVector("mail_subject", weight="B", config="german")
            + SearchVector("mail_sender", weight="B", config="german")
            + SearchVector("note", weight="B", config="german")
            + SearchVector("current_version__ocr_text", weight="D", config="german")
        )
        search_query = SearchQuery(q, config="german")
        qs = qs.annotate(rank=SearchRank(vector, search_query)).filter(rank__gt=0)

    if query.get("correspondent"):
        qs = qs.filter(correspondent_id=query["correspondent"])
    if query.get("document_type"):
        qs = qs.filter(document_type_id=query["document_type"])
    if query.get("storage_path"):
        qs = qs.filter(storage_path_id=query["storage_path"])
    if query.get("case_file"):
        qs = qs.filter(case_file_id=query["case_file"])

    folder = query.get("folder")
    if folder == "none":
        qs = qs.filter(folder__isnull=True)
    elif folder:
        qs = qs.filter(folder_id=folder)

    review_status = query.get("review_status")
    if review_status in {choice for choice, _label in Document.ReviewStatus.choices}:
        qs = qs.filter(review_status=review_status)

    processing_state = query.get("processing_state")
    if processing_state:
        PS = DocumentVersion.ProcessingState
        buckets = {
            "failed": [PS.FAILED],
            "retry_pending": [PS.RETRY_PENDING],
            "ready": [PS.READY],
            "processing": [
                PS.UPLOADED,
                PS.HASHED,
                PS.OCR_RUNNING,
                PS.OCR_DONE,
                PS.CLASSIFICATION_RUNNING,
                PS.CLASSIFIED,
                PS.THUMBNAIL_DONE,
                PS.SEALED,
            ],
        }
        states = buckets.get(processing_state)
        if states is None and processing_state in {c for c, _ in PS.choices}:
            states = [processing_state]
        if states is not None:
            qs = qs.filter(current_version__processing_state__in=states)

    tag = query.get("tag")
    if tag:
        values = tag if isinstance(tag, list) else [tag]
        qs = qs.filter(tags__id__in=values)

    return _apply_custom_field_filters(qs, query.get("customFilters")).distinct()


def count_documents_for_query(user, query) -> int:
    return filter_documents_for_query(user, query).count()
