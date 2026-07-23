import hashlib
import io
import os
import re
import secrets
import shutil
import tempfile
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation

import img2pdf
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files import File
from django.db import connection, transaction
from django.db.models import Case, DecimalField, F, Q, Value, When
from django.db.models import Count, IntegerField, Max, OuterRef, Prefetch, Subquery
from django.db.models.functions import Coalesce
from django.db.models.functions import Cast
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseNotModified,
)
from django.utils import timezone
from django.utils.text import slugify
from kombu.exceptions import OperationalError as BrokerOperationalError
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import APIException
from rest_framework.filters import OrderingFilter
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import (
    SAFE_METHODS,
    AllowAny,
    BasePermission,
    IsAuthenticated,
)
from rest_framework.response import Response
from rest_framework.views import APIView


class ReadOnlyOrCanWrite(BasePermission):
    """Lesen für alle Angemeldeten; Schreiben nur für can_write (nicht Gäste)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return bool(getattr(request.user, "can_write", False))


class ReadCreateOrAdminMutate(BasePermission):
    """Für GLOBALE, von allen Nutzern geteilte Stammdaten (Tags, Korrespondenten,
    Dokumenttypen, Ablagepfade, Zusatzfeld-Definitionen):

    * Lesen (SAFE) – jeder Angemeldete.
    * Anlegen (POST) – ``can_write`` (nötig fürs inline-Anlegen beim Ablegen).
    * Umbenennen/Löschen (PUT/PATCH/DELETE) – NUR Admins.

    Sonst könnte ein Nutzer Metadaten ALLER Nutzer global umbenennen oder löschen
    (ein Tag/Korrespondent hängt an fremden Dokumenten). Anlegen bleibt erlaubt,
    weil es additiv/ungefährlich ist.
    """

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        if request.method == "POST":
            return bool(getattr(user, "can_write", False))
        return bool(getattr(user, "is_dms_admin", False))


class IsDmsAdmin(BasePermission):
    """Nur DMS-Administratoren (``is_dms_admin``) – für Systemkonfiguration.

    Verwendet für die Mailkonto-Verwaltung (STOAA-212): IMAP-Zugangsdaten sind
    sensibel, deshalb ist der gesamte ViewSet (auch Lesen) Admins vorbehalten.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return bool(getattr(request.user, "is_dms_admin", False))

from . import classification, pipeline, storage
from .filetypes import SNIFF_BYTES, UnsupportedFileType, detect, is_safe_inline
from .throttling import AiRateThrottle, CaptureRateThrottle, UploadRateThrottle
from .services import version_compare
from .models import (
    AuditLogEntry,
    BackupMonitor,
    BackupRun,
    CaseFile,
    CaseFileCandidate,
    ClassificationRule,
    Correspondent,
    ContractRecord,
    CustomField,
    CustomFieldValue,
    Document,
    Dossier,
    DocumentEntity,
    DocumentFolder,
    DocumentReminder,
    DocumentReviewTask,
    DocumentShareLink,
    DocumentType,
    DocumentVersion,
    EntityRelation,
    ExtractionCandidate,
    KnowledgeEntity,
    MailAccount,
    OCRStatus,
    ProcessedMail,
    SavedView,
    StoragePath,
    Tag,
    Workflow,
)
from .serializers import (
    AuditLogEntrySerializer,
    CaseFileCandidateSerializer,
    CaseFileSerializer,
    ClassificationRuleSerializer,
    CorrespondentSerializer,
    ContractRecordSerializer,
    CustomFieldSerializer,
    DocumentReminderSerializer,
    DocumentReviewTaskSerializer,
    DocumentListSerializer,
    DocumentSerializer,
    DossierSerializer,
    DocumentFolderSerializer,
    DocumentShareLinkSerializer,
    DocumentTypeSerializer,
    DocumentVersionSerializer,
    DocumentEntitySerializer,
    EntityRelationSerializer,
    ExtractionCandidateSerializer,
    KnowledgeEntitySerializer,
    MailAccountSerializer,
    ProcessedMailSerializer,
    SavedViewSerializer,
    StoragePathSerializer,
    TagSerializer,
    WorkflowSerializer,
)
from .services import asn as asn_service
from .services import archive as archive_service
from .services import contracts as contract_service
from .services import document_briefing as document_briefing_service
from .services import dossiers as dossier_service
from .services import evidence as evidence_service
from .services import entity_graph as entity_graph_service
from .services import quality as quality_service
from .services import auto_file as auto_file_service
from .services import duplicates as duplicates_service
from .services import review_tasks as review_task_service
from .services import revision_package as revision_package_service
from .services import spending as spending_service
from .services import semantic_index as semantic_index_service
from .services import timeline as timeline_service
from .tasks import (
    bulk_classify_documents,
    enqueue_processing,
    process_document_version,
    retry_document_version,
)


class _ProcessingUnavailable(APIException):
    """HTTP 503: der Celery-Broker (Redis) ist gerade nicht erreichbar.

    DRF wandelt eine geworfene ``APIException`` selbst in eine saubere Antwort –
    hier 503 statt eines kryptischen 500. Bewusst KEIN 500: das Dokument/die
    Version wurde bereits erzeugt und ist per Retry nachverarbeitbar, sobald der
    Broker wieder da ist; der Client soll das als „später erneut" verstehen.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = (
        "Hintergrundverarbeitung ist derzeit nicht erreichbar. Das Dokument "
        "wurde gespeichert; bitte die Verarbeitung in Kürze erneut anstoßen."
    )
    default_code = "processing_unavailable"


def _enqueue_processing(version_id) -> None:
    """``process_document_version`` anstoßen; Broker-Ausfall → sauberes 503.

    Delegiert an den gemeinsamen ``tasks.enqueue_processing`` (derselbe Pfad wie
    Mail/Consume): der markiert die – bereits committete – Version bei einem
    Broker-Ausfall als FAILED (retry-fähig) statt sie in UPLOADED hängen zu
    lassen. Für den interaktiven Request wird der Fehlschlag hier in ein HTTP 503
    übersetzt (statt eines kryptischen 500). IMMER erst NACH der Versions-
    Erzeugung aufrufen.
    """
    version = DocumentVersion.objects.filter(pk=version_id).first()
    if version is None:
        # Kein Objekt (Race o. Ä.): best-effort Enqueue; Broker-Fehler → 503.
        try:
            process_document_version.delay(version_id)
        except BrokerOperationalError as exc:
            raise _ProcessingUnavailable() from exc
        return
    if not enqueue_processing(version):
        raise _ProcessingUnavailable()

# Erkennt Bereichsfilter auf Zusatzfeldern: ``custom_field_<id>_gte`` / ``_lte``.
_CUSTOM_FIELD_PARAM_RE = re.compile(r"^custom_field_(\d+)_(gte|lte)$")
# Eine Sucheingabe, die *ausschließlich* eine ASN ist: ``ASN12345`` oder die reine
# Nummer ``12345`` (führende Nullen erlaubt). Beide Formen sind für die Suche
# äquivalent und liefern exakt das zugehörige Dokument (STOAA-284/285).
_ASN_QUERY_RE = re.compile(r"(?i)^\s*(?:asn)?\s*[0-9]+\s*$")
# Werte, die sich verlustfrei zu DECIMAL casten lassen (Vorzeichen + Dezimalpunkt).
# Andere ``CustomFieldValue.value`` (Text/Datum/„k. A.") werden per CASE zu NULL
# und fallen aus dem Vergleich – kein Postgres-500 beim Cast.
_NUMERIC_VALUE_RE = r"^-?[0-9]+(\.[0-9]+)?$"
_DECIMAL_OUTPUT = DecimalField(max_digits=30, decimal_places=10)
_EXTRACTION_CUSTOM_FIELD_TARGETS = {
    ExtractionCandidate.Field.AMOUNT: ("Betrag", CustomField.DataType.CURRENCY),
    ExtractionCandidate.Field.IBAN: ("IBAN", CustomField.DataType.TEXT),
    ExtractionCandidate.Field.CONTRACT_NUMBER: (
        "Vertragsnummer",
        CustomField.DataType.TEXT,
    ),
    ExtractionCandidate.Field.POLICY_NUMBER: (
        "Versicherungsnummer",
        CustomField.DataType.TEXT,
    ),
}


def _household_member_ids(user) -> set:
    """IDs aller Nutzer, die mit ``user`` einen Haushalt teilen (inkl. user selbst).

    Basis der Familien-Freigabe: ein für den Haushalt freigegebenes Dokument ist
    für genau diese Nutzer LESBAR. Ohne Haushalt → nur der Nutzer selbst (dann ist
    die Freigabe wirkungslos, kein Leak).
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    ids = set(
        User.objects.filter(households__members=user).values_list("id", flat=True)
    )
    ids.add(user.id)
    return ids


def _folder_share_map() -> dict:
    """Bildet ``folder_id -> {sharer_owner_ids}`` ab: die Owner der Ordner, die in
    der Elternkette dieses Ordners freigegeben sind (Vererbung auf Unterordner).

    Sicherheits-Anker: Eine Ordnerfreigabe wirkt NUR für Dokumente des Ordner-
    Owners. Ein ownerloser (alt-globaler) freigegebener Ordner trägt niemanden bei
    – seine Freigabe exponiert damit nichts (kein Leak fremder Dokumente).
    Der Ordnerbaum ist klein (Familien-DMS) → einmaliges Laden reicht.
    """
    rows = list(
        DocumentFolder.objects.values_list(
            "id", "parent_id", "shared_with_household", "owner_id"
        )
    )
    parent = {fid: pid for fid, pid, _, _ in rows}
    owner = {fid: oid for fid, _, _, oid in rows}
    shared = {fid for fid, _, is_shared, _ in rows if is_shared}
    if not shared:
        return {}
    result: dict = {}
    for fid, _pid, _is_shared, _oid in rows:
        cursor, seen, owners = fid, set(), set()
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            if cursor in shared and owner.get(cursor) is not None:
                owners.add(owner[cursor])
            cursor = parent.get(cursor)
        if owners:
            result[fid] = owners
    return result


def _household_visibility_q(user):
    """Q für die LESE-Sichtbarkeit: eigene Dokumente + haushaltsgeteilte.

    Haushaltsgeteilt = Eigentümer ist Haushalts-Mitmitglied UND
      * das Dokument selbst ist freigegeben (``shared_with_household``), ODER
      * das Dokument liegt in einem freigegebenen Ordner/Unterordner, DESSEN Owner
        der Dokument-Eigentümer ist (man teilt nur die EIGENEN Dokumente per
        Ordner – nicht fremde, die jemand hineingelegt hat).
    Die Sichtbarkeit bleibt strikt an der Haushalts-Mitgliedschaft verankert –
    niemals über Haushalte hinweg. EINE Quelle für alle Lese-Kontexte.
    """
    member_ids = _household_member_ids(user)
    q = Q(owner=user) | (Q(owner_id__in=member_ids) & Q(shared_with_household=True))

    # Ordnerbasierte Freigabe: nur Dokumente, deren Owner den (in der Kette)
    # freigegebenen Ordner besitzt UND mit ``user`` einen Haushalt teilt.
    for folder_id, sharer_ids in _folder_share_map().items():
        eligible = sharer_ids & member_ids
        if eligible:
            q |= Q(folder_id=folder_id, owner_id__in=eligible)
    return q


def _visible_documents_for(user):
    """Lese-Sichtbarkeit: eigene Dokumente + für den Haushalt freigegebene."""
    qs = Document.objects.select_related(
        "correspondent",
        "document_type",
        "folder",
        "case_file",
        "current_version",
    )
    if not getattr(user, "is_dms_admin", False):
        qs = qs.filter(_household_visibility_q(user))
    return qs


def _parse_days(raw_value, *, default: int) -> int:
    try:
        days = int(raw_value if raw_value not in ("", None) else default)
    except (TypeError, ValueError):
        days = default
    return max(0, min(days, 365))


def _apply_custom_field_filters(qs, params):
    """Wendet dynamische ``custom_field_<id>_gte/_lte``-Bereichsfilter an.

    Der TextField ``CustomFieldValue.value`` wird für den Vergleich nach DECIMAL
    gecastet – aber nur für rein numerische Werte (regex-geschütztes ``CASE``),
    damit nicht-numerische Werte keinen Cast-Fehler (500) auslösen. Ungültige
    Grenzwerte im Query-Param werden ignoriert (kein 500). Mehrere Grenzen auf
    dasselbe Feld (gte + lte) verknüpfen additiv (AND) über getrennte Subqueries.
    """
    for key in params:
        match = _CUSTOM_FIELD_PARAM_RE.match(key)
        if not match:
            continue
        field_id, op = int(match.group(1)), match.group(2)
        try:
            bound = Decimal(params.get(key))
        except (InvalidOperation, TypeError, ValueError):
            continue  # ungültige Grenze → Filter ignorieren, nie 500

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


def _clean(value, max_len=255) -> str:
    """Normalisiert einen Vorschlagswert: strip + Längen-Cap.

    Nicht-String-Werte werden robust ignoriert (leerer String), ebenso Leerwerte
    nach dem Strippen. So können fehlerhafte KI-Vorschläge nie ungeprüft in die
    Stammdaten gelangen.
    """
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def _get_or_create_ci(model, name, **extra):
    """Case-insensitive get-or-create über ``name`` (verhindert Duplikate).

    "Finanzamt" und "finanzamt" gelten als derselbe Bestandswert – existiert
    bereits einer, wird er wiederverwendet; sonst mit dem gereinigten Namen neu
    angelegt. ``extra`` grenzt die Suche zusätzlich ein (z. B. ``parent=None``).
    """
    obj = model.objects.filter(name__iexact=name, **extra).first()
    if obj is None:
        obj = model.objects.create(name=name, **extra)
    return obj


def _parse_iso_date(raw):
    """Parst ein striktes ISO-Datum (``YYYY-MM-DD``) zu tz-awarem 00:00 Uhr UTC.

    Gibt ``None`` bei Nicht-Strings, Leerwerten oder ungültigem Format zurück –
    der Aufrufer ignoriert solche Werte still (kein Fehler). Das Belegdatum ist
    ein reines Kalenderdatum; wir verankern es bewusst auf **UTC-Mitternacht**
    (nicht lokale Mitternacht), damit ``created_at.date()`` unabhängig von der
    Lese-Zeitzone das eingegebene Datum ergibt. Lokale Mitternacht (Europe/Berlin)
    würde in der DB als Vortag-22:00 UTC landen und beim Zurücklesen um einen Tag
    kippen.
    """
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        parsed = date_cls.fromisoformat(raw)
    except ValueError:
        return None
    dt = datetime.combine(parsed, time.min)
    if settings.USE_TZ:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Health-Check für Frontend & k8s-Probes.

    Prüft die DB-Verbindung; meldet Version/Status als JSON.
    """
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # pragma: no cover - Diagnose
        db_ok = False

    status = "ok" if db_ok else "degraded"
    return Response(
        {
            "status": status,
            "service": "dms-backend",
            # Beim Build injiziert (Git-SHA-Tag) – zeigt den WIRKLICH laufenden Commit.
            "version": getattr(settings, "APP_VERSION", "dev"),
            "commit": getattr(settings, "GIT_SHA", ""),
            "database": "ok" if db_ok else "unreachable",
        },
        status=200 if db_ok else 503,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def livez(request):
    """Liveness-Probe: prüft NUR, ob der Web-Prozess antwortet – KEINE DB.

    Getrennt von ``health`` (Readiness inkl. DB): Ein DB-Ausfall soll den Pod
    aus dem Service nehmen (Readiness 503), aber NICHT zusätzlich Neustarts
    auslösen (Liveness). Sonst rebooten bei jedem DB-Blip alle Backend-Pods.
    """
    return Response({"status": "alive", "service": "dms-backend"}, status=200)


class BackupStatusView(APIView):
    """Admin-only Betriebsstatus für Backup-CronJob und Restore-Drill."""

    permission_classes = [IsDmsAdmin]

    def get(self, request):
        now = timezone.now()
        alert_after_hours = float(getattr(settings, "BACKUP_ALERT_AFTER_HOURS", 36))
        states = {
            item.kind: item
            for item in BackupMonitor.objects.filter(
                kind__in=[
                    BackupMonitor.Kind.BACKUP,
                    BackupMonitor.Kind.RESTORE_DRILL,
                ]
            )
        }

        backup = self._serialize_state(
            states.get(BackupMonitor.Kind.BACKUP),
            now=now,
            alert_after_hours=alert_after_hours,
            stale_sensitive=True,
        )
        restore_drill = self._serialize_state(
            states.get(BackupMonitor.Kind.RESTORE_DRILL),
            now=now,
            alert_after_hours=None,
            stale_sensitive=False,
        )

        cronjob = {
            "name": "backup",
            "schedule": "0 2 * * *",
            "expected_interval_hours": 24,
            "alert_after_hours": alert_after_hours,
            "last_run_status": backup["status"],
            "last_success_at": backup["last_success_at"],
            "stale": backup["stale"],
        }

        overall = "ok"
        if backup["status"] == BackupMonitor.Status.FAILED or restore_drill[
            "status"
        ] == BackupMonitor.Status.FAILED:
            overall = "error"
        elif backup["stale"] or backup["status"] in (
            BackupMonitor.Status.UNKNOWN,
            BackupMonitor.Status.RUNNING,
        ):
            overall = "warn"

        history = {
            BackupMonitor.Kind.BACKUP: self._recent_runs(BackupMonitor.Kind.BACKUP),
            BackupMonitor.Kind.RESTORE_DRILL: self._recent_runs(
                BackupMonitor.Kind.RESTORE_DRILL
            ),
        }

        return Response(
            {
                "status": overall,
                "generated_at": now.isoformat(),
                "backup": backup,
                "cronjob": cronjob,
                "restore_drill": restore_drill,
                "history": history,
            }
        )

    @staticmethod
    def _recent_runs(kind, *, limit=10):
        """Die letzten ``limit`` Läufe eines ``kind`` – neueste zuerst, für Trend."""
        runs = BackupRun.objects.filter(kind=kind).order_by("-created_at")[:limit]
        return [
            {
                "status": run.status,
                "artifact_timestamp": run.artifact_timestamp,
                "size_bytes": run.size_bytes,
                "message": run.message,
                "created_at": run.created_at.isoformat() if run.created_at else None,
            }
            for run in runs
        ]

    @staticmethod
    def _serialize_state(item, *, now, alert_after_hours, stale_sensitive):
        if item is None:
            return {
                "status": BackupMonitor.Status.UNKNOWN,
                "artifact_timestamp": "",
                "message": "",
                "size_bytes": None,
                "last_started_at": None,
                "last_success_at": None,
                "last_finished_at": None,
                "updated_at": None,
                "age_hours": None,
                "stale": bool(stale_sensitive),
            }

        age_hours = None
        stale = False
        if item.last_success_at:
            age_hours = round((now - item.last_success_at).total_seconds() / 3600, 2)
            if stale_sensitive and alert_after_hours is not None:
                stale = age_hours > alert_after_hours
        elif stale_sensitive:
            stale = True

        return {
            "status": item.status,
            "artifact_timestamp": item.artifact_timestamp,
            "message": item.message,
            "size_bytes": item.size_bytes,
            "last_started_at": item.last_started_at.isoformat()
            if item.last_started_at
            else None,
            "last_success_at": item.last_success_at.isoformat()
            if item.last_success_at
            else None,
            "last_finished_at": item.last_finished_at.isoformat()
            if item.last_finished_at
            else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            "age_hours": age_hours,
            "stale": stale,
        }


class SemanticIndexHealthView(APIView):
    """Admin-only Status des semantischen Dokumentindex."""

    permission_classes = [IsDmsAdmin]

    def get(self, request):
        return Response(semantic_index_service.embedding_health())


class ArchiveHealthView(APIView):
    """Admin-only Archiv-/Retention-Status aus gespeicherten Prüfresultaten."""

    permission_classes = [IsDmsAdmin]

    def get(self, request):
        return Response(archive_service.archive_health())

    def post(self, request):
        raw_limit = request.data.get("limit", 50) if hasattr(request, "data") else 50
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 250))

        candidates = (
            Document.objects.select_related("current_version")
            .prefetch_related("versions")
            .filter(
                Q(archive_status=Document.ArchiveStatus.UNCHECKED)
                | Q(archive_status=Document.ArchiveStatus.ERROR)
                | Q(archive_checked_at__isnull=True)
            )
            .order_by("archive_checked_at", "id")[:limit]
        )
        checked = []
        for document in candidates:
            checked.append(archive_service.verify_document_archive(document))

        AuditLogEntry.objects.create(
            actor=request.user,
            action="archive_bulk_check",
            object_type="System",
            object_id="archive",
            detail={"checked": len(checked), "limit": limit},
        )
        return Response(
            {
                "checked": len(checked),
                "limit": limit,
                "health": archive_service.archive_health(),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class OCRHealthView(APIView):
    """Admin-only Qualitätsdashboard für OCR und Dokumentverarbeitung."""

    permission_classes = [IsDmsAdmin]

    def get(self, request):
        now = timezone.now()
        threshold_rate = float(getattr(settings, "OCR_ALERT_SUCCESS_RATE", 95))
        stuck_after_minutes = float(
            getattr(settings, "PROCESSING_STUCK_AFTER_MINUTES", 30)
        )
        stuck_before = now - timedelta(minutes=stuck_after_minutes)
        current_versions = self._current_versions()

        total = current_versions.count()
        skipped = current_versions.filter(ocr_status=OCRStatus.SKIPPED).count()
        denominator = max(total - skipped, 0)
        ocr_success = current_versions.filter(ocr_status=OCRStatus.SUCCESS).count()
        ocr_failed = current_versions.filter(ocr_status=OCRStatus.FAILED).count()
        ocr_running = current_versions.filter(ocr_status=OCRStatus.RUNNING).count()
        ocr_pending = current_versions.filter(ocr_status=OCRStatus.PENDING).count()
        empty_ocr_text = (
            current_versions.exclude(ocr_status=OCRStatus.SKIPPED)
            .filter(ocr_text="")
            .count()
        )

        PS = DocumentVersion.ProcessingState
        inflight_states = [
            PS.UPLOADED,
            PS.HASHED,
            PS.OCR_RUNNING,
            PS.OCR_DONE,
            PS.CLASSIFICATION_RUNNING,
            PS.CLASSIFIED,
            PS.THUMBNAIL_DONE,
            PS.SEALED,
        ]
        processing_failed = current_versions.filter(processing_state=PS.FAILED).count()
        retry_pending = current_versions.filter(
            processing_state=PS.RETRY_PENDING
        ).count()
        processing_ready = current_versions.filter(processing_state=PS.READY).count()
        # „Hängend" am ZUSTANDS-Zeitstempel messen, nicht an created_at: sonst
        # gilt eine alte Version, deren Retry gerade frisch gestartet wurde, sofort
        # als hängend. processing_state_changed_at ist derselbe Wert, den der
        # Watchdog nutzt (konsistent).
        stuck_qs = current_versions.filter(
            processing_state__in=inflight_states,
            processing_state_changed_at__lt=stuck_before,
        )
        stuck_count = stuck_qs.count()
        oldest_stuck = stuck_qs.order_by("processing_state_changed_at").first()

        success_rate = (
            round((ocr_success / denominator) * 100, 1) if denominator else 100.0
        )

        status_value = "ok"
        if processing_failed:
            status_value = "error"
        elif (
            success_rate < threshold_rate
            or empty_ocr_text
            or ocr_failed
            or stuck_count
        ):
            status_value = "warn"

        issues = self._issue_rows(
            current_versions,
            stuck_before=stuck_before,
            inflight_states=inflight_states,
        )

        return Response(
            {
                "status": status_value,
                "generated_at": now.isoformat(),
                "thresholds": {
                    "ocr_success_rate": threshold_rate,
                    "processing_stuck_after_minutes": stuck_after_minutes,
                },
                "summary": {
                    "total_current_versions": total,
                    "ocr_success": ocr_success,
                    "ocr_failed": ocr_failed,
                    "ocr_running": ocr_running,
                    "ocr_pending": ocr_pending,
                    "ocr_skipped": skipped,
                    "empty_ocr_text": empty_ocr_text,
                    "ocr_success_rate": success_rate,
                    "processing_ready": processing_ready,
                    "processing_failed": processing_failed,
                    "retry_pending": retry_pending,
                    "stuck_processing": stuck_count,
                },
                "oldest_stuck": self._serialize_issue(oldest_stuck)
                if oldest_stuck
                else None,
                "issues": [self._serialize_issue(v) for v in issues],
            }
        )

    @staticmethod
    def _current_versions():
        current_ids = Document.objects.exclude(current_version_id__isnull=True).values(
            "current_version_id"
        )
        return DocumentVersion.objects.filter(pk__in=current_ids).select_related(
            "document"
        )

    @classmethod
    def _issue_rows(cls, current_versions, *, stuck_before, inflight_states, limit=25):
        PS = DocumentVersion.ProcessingState
        issue_filter = (
            Q(processing_state=PS.FAILED)
            | Q(ocr_status=OCRStatus.FAILED)
            | (Q(ocr_text="") & ~Q(ocr_status=OCRStatus.SKIPPED))
            # Hängende Versionen (Zwischenzustand länger als der Schwellwert)
            # gehören ebenfalls in die Issue-Liste, auch OHNE OCR-Fehler.
            | (
                Q(processing_state__in=inflight_states)
                & Q(processing_state_changed_at__lt=stuck_before)
            )
        )
        return current_versions.filter(issue_filter).order_by(
            "-processing_failed_at", "-ocr_finished_at", "-processing_state_changed_at"
        )[:limit]

    @staticmethod
    def _serialize_issue(version):
        if version is None:
            return None
        return {
            "document_id": version.document_id,
            "document_title": version.document.title,
            "version_id": version.id,
            "version_no": version.version_no,
            "processing_state": version.processing_state,
            "processing_error": version.processing_error,
            "processing_failed_step": version.processing_failed_step,
            "processing_failed_at": version.processing_failed_at.isoformat()
            if version.processing_failed_at
            else None,
            "processing_state_changed_at": (
                version.processing_state_changed_at.isoformat()
                if version.processing_state_changed_at
                else None
            ),
            "processing_attempts": version.processing_attempts,
            "ocr_status": version.ocr_status,
            "ocr_error": version.ocr_error,
            "ocr_started_at": version.ocr_started_at.isoformat()
            if version.ocr_started_at
            else None,
            "ocr_finished_at": version.ocr_finished_at.isoformat()
            if version.ocr_finished_at
            else None,
            "ocr_text_length": len(version.ocr_text or ""),
            "created_at": version.created_at.isoformat() if version.created_at else None,
            "can_retry": version.processing_state
            == DocumentVersion.ProcessingState.FAILED,
        }


class OCRRetryFailedView(APIView):
    """Bulk-Retry für fehlgeschlagene aktuelle Dokumentversionen."""

    permission_classes = [IsDmsAdmin]

    def post(self, request):
        raw_limit = request.data.get("limit", 25) if hasattr(request, "data") else 25
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 25
        limit = max(1, min(limit, 100))

        PS = DocumentVersion.ProcessingState
        versions = list(
            OCRHealthView._current_versions()
            .filter(processing_state=PS.FAILED)
            .order_by("processing_failed_at", "id")[:limit]
        )
        for version in versions:
            retry_document_version.delay(version.id, actor_id=request.user.id)

        return Response(
            {
                "queued": len(versions),
                "limit": limit,
                "version_ids": [v.id for v in versions],
            },
            status=status.HTTP_202_ACCEPTED,
        )


class TimelineView(APIView):
    """Zentrale Fristen-/Timeline-API über Erinnerungen, Verträge und Aufgaben."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        days = _parse_days(request.query_params.get("days"), default=30)
        return Response(
            timeline_service.build_timeline(
                _visible_documents_for(request.user),
                days=days,
            )
        )


class TimelineICSView(APIView):
    """All-Day-iCalendar-Export der sichtbaren Fristen."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        days = _parse_days(request.query_params.get("days"), default=90)
        content = timeline_service.build_ics(
            _visible_documents_for(request.user),
            days=days,
        )
        response = HttpResponse(content, content_type="text/calendar; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="dms-fristen.ics"'
        return response


class AskView(APIView):
    """Dokumenten-Copilot: beantwortet Fragen anhand sichtbarer OCR-Quellen."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [AiRateThrottle]  # KI-Kosten/Last begrenzen (P2)

    def post(self, request):
        question = str(request.data.get("question", "")).strip()
        if len(question) < 3:
            return Response(
                {"detail": "Feld 'question' muss mindestens 3 Zeichen enthalten."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Haushalts-Sichtbarkeit (eigene + freigegebene) EINHEITLICH über den
        # zentralen Helfer – sonst verschwinden geteilte Dokumente aus dem Copilot.
        qs = (
            _visible_documents_for(request.user)
            .select_related("contract_record")
            .prefetch_related("tags", "current_version__page_texts")
            .exclude(current_version__isnull=True)
            .order_by("-added_at")
        )

        folder = request.data.get("folder")
        if folder in ("", None):
            pass
        elif folder == "none":
            qs = qs.filter(folder__isnull=True)
        else:
            try:
                qs = qs.filter(folder_id=int(folder))
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Ungültiger Ordnerfilter."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        case_file = request.data.get("case_file")
        if case_file not in ("", None):
            try:
                qs = qs.filter(case_file_id=int(case_file))
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Ungültiger Aktenfilter."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        entity = request.data.get("entity")
        if entity not in ("", None):
            try:
                qs = qs.filter(entity_links__entity_id=int(entity))
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Ungültiger Entitätsfilter."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        contract = request.data.get("contract")
        if contract not in ("", None):
            try:
                qs = qs.filter(contract_record__id=int(contract))
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Ungültiger Vertragsfilter."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        created_from = request.data.get("created_from")
        created_to = request.data.get("created_to")
        if created_from:
            try:
                qs = qs.filter(created_at__date__gte=date_cls.fromisoformat(created_from))
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Ungültiger Datumsfilter 'created_from'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if created_to:
            try:
                qs = qs.filter(created_at__date__lte=date_cls.fromisoformat(created_to))
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Ungültiger Datumsfilter 'created_to'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        from ai.services import answer_question
        from .services.retrieval import RetrievalFilters

        retrieval_filters = RetrievalFilters(
            folder=folder,
            case_file=int(case_file) if case_file not in ("", None) else None,
            entity=int(entity) if entity not in ("", None) else None,
            contract=int(contract) if contract not in ("", None) else None,
            created_from=created_from or None,
            created_to=created_to or None,
        )
        result = answer_question(
            question,
            qs.distinct()[:300],
            filters=retrieval_filters,
        )
        AuditLogEntry.objects.create(
            actor=request.user,
            action="ask",
            object_type="Document",
            object_id="Copilot",
            detail={
                "question": question[:500],
                "filters": retrieval_filters.as_dict(),
                "source": result.get("source"),
                "sources": [s.get("document") for s in result.get("sources", [])],
            },
        )
        return Response(result, status=status.HTTP_200_OK)


class SemanticSearchView(APIView):
    """Bedeutungssuche: findet Dokumente über semantische Ähnlichkeit (pgvector).

    Anders als die Volltextsuche (Lexeme/ILIKE) matcht diese Suche die *Bedeutung*
    der Anfrage gegen fastembed/e5-Embeddings der OCR-Chunks. Owner-gescoped wie
    die restliche API. Nimmt ``q`` (oder ``question``) via GET-Query oder POST-Body.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [AiRateThrottle]  # KI-Kosten/Last begrenzen (P2)

    def get(self, request):
        return self._run(request, request.query_params)

    def post(self, request):
        return self._run(request, request.data)

    def _run(self, request, data):
        question = str(data.get("q") or data.get("question") or "").strip()
        if len(question) < 3:
            return Response(
                {"detail": "Feld 'q' muss mindestens 3 Zeichen enthalten."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            limit = int(data.get("limit", 8))
        except (TypeError, ValueError):
            limit = 8
        limit = max(1, min(limit, 20))

        # Haushalts-Sichtbarkeit EINHEITLICH über den zentralen Helfer (nicht nur
        # owner=request.user) – sonst fehlen geteilte Dokumente in der Bedeutungssuche.
        qs = _visible_documents_for(request.user).exclude(current_version__isnull=True)

        results = semantic_index_service.search_documents(question, qs, limit=limit)
        from ai import embeddings as _embeddings

        return Response(
            {
                "query": question,
                "count": len(results),
                "results": results,
                "model": semantic_index_service.EMBEDDING_MODEL,
                "enabled": _embeddings.enabled(),
            },
            status=status.HTTP_200_OK,
        )


class HybridSearchView(APIView):
    """Hybride Suche: Volltext (FTS) + Semantik in EINEM Ranking (RRF).

    Vereint die Präzision der PostgreSQL-Volltextsuche mit der Trefferquote der
    semantischen Suche. Haushalts-Sichtbarkeit wie die Liste (eigene + für die
    Familie freigegebene). ``q`` via GET-Query oder POST-Body.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return self._run(request, request.query_params)

    def post(self, request):
        return self._run(request, request.data)

    def _run(self, request, data):
        query = str(data.get("q") or data.get("question") or "").strip()
        if len(query) < 3:
            return Response(
                {"detail": "Feld 'q' muss mindestens 3 Zeichen enthalten."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            limit = int(data.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 25))

        from .services import hybrid_search as hybrid_search_service

        qs = _visible_documents_for(request.user).exclude(current_version__isnull=True)
        results = hybrid_search_service.hybrid_search(qs, query, limit=limit)
        return Response(
            {"query": query, "count": len(results), "results": results},
            status=status.HTTP_200_OK,
        )


class AgentPlanView(APIView):
    """Copilot-Agent: schlägt aus einer Anweisung einen bestätigbaren Aktionsplan vor.

    Führt NICHTS aus – gibt nur Vorschläge zurück (whitelisted, owner-gescoped auf
    eigene Dokumente). Der Nutzer bestätigt anschließend über ``AgentExecuteView``.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        from .services import agent

        instruction = str(request.data.get("instruction", "")).strip()
        return Response(agent.plan(request.user, instruction))


class AgentExecuteView(APIView):
    """Führt vom Nutzer bestätigte Agent-Aktionen deterministisch aus (owner-scoped)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        from .services import agent

        result = agent.execute(request.user, request.data.get("actions", []))
        return Response(result)


class AgentUndoView(APIView):
    """Macht eine zuvor ausgeführte Agent-Aktion rückgängig (owner-gescoped).

    Nutzt die beim Ausführen im Audit-Eintrag hinterlegte Umkehr-Information; die
    Umkehr selbst ist deterministisch (kein LLM). Doppeltes Undo wird erkannt.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        from .services import agent

        return Response(agent.undo(request.user, request.data.get("audit_id")))


class DocumentUploadView(APIView):
    """Nimmt eine Datei per multipart/form-data auf und stößt die Pipeline an.

    Felder: ``file`` (Pflicht), ``title`` (optional; Standard = Dateiname).
    Antwortet mit dem angelegten Dokument; OCR läuft asynchron im Worker.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [UploadRateThrottle]  # DoS-Schutz (P1)

    def post(self, request):
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        uploaded = request.FILES.get("file")
        if uploaded is None:
            return Response(
                {"detail": "Feld 'file' fehlt."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        title = request.data.get("title") or uploaded.name.rsplit(".", 1)[0]
        try:
            file_path, size, mime = storage.save_upload(uploaded)
        except UnsupportedFileType as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        document, version = pipeline.create_document_from_file(
            file_path, title=title, owner=request.user, mime=mime, size=size
        )
        # OCR/Hash-Kette asynchron im Celery-Worker.
        _enqueue_processing(version.id)

        return Response(
            DocumentSerializer(document).data,
            status=status.HTTP_201_CREATED,
        )


class _InvalidImage(Exception):
    """Interne Markierung: hochgeladene Datei ist kein verarbeitbares Bild."""


def _mobile_capture_max_pixels() -> int:
    return int(getattr(settings, "MOBILE_CAPTURE_MAX_IMAGE_PIXELS", 40_000_000))


def _mobile_capture_max_dimension() -> int:
    return int(getattr(settings, "MOBILE_CAPTURE_MAX_DIMENSION", 4000))


def _reject_if_pixel_bomb(raw: bytes) -> None:
    """Weist Bilder ab, deren Pixelzahl das Limit sprengt – BEVOR sie dekodiert
    oder (via img2pdf) verlustfrei eingebettet werden.

    Schützt (a) den Web-Prozess vor Decompression-Bombs beim Pillow-Decode und
    (b) den späteren OCR-Schritt (rastert eingebettete Riesenbilder). ``Image.open``
    liest die Maße lazy (ohne Voll-Dekodierung). Nicht lesbare Bytes werden hier
    durchgelassen – img2pdf/Pillow entscheiden dann.
    """
    from PIL import Image

    try:
        with Image.open(io.BytesIO(raw)) as im:
            width, height = im.size
    except Exception:  # noqa: BLE001 – hier nur die Größe prüfen; Format später
        return
    max_pixels = _mobile_capture_max_pixels()
    if width * height > max_pixels:
        raise _InvalidImage(
            f"Bild zu groß ({width}x{height} Pixel > Limit {max_pixels})."
        )


def _pillow_to_jpeg(raw: bytes) -> bytes:
    """Öffnet Bytes mit Pillow, flacht nach RGB ab und liefert JPEG Q90.

    Deckt HEIC/HEIF (nach ``register_heif_opener``), PNG mit Alpha und
    CMYK-JPEG ab – Formate, die ``img2pdf`` sonst ablehnt. Kann Pillow das
    Bild nicht öffnen, wird ``_InvalidImage`` geworfen (→ 400 im View).

    RAM-Schutz: zu pixelreiche Bilder werden abgewiesen (Decompression-Bomb) und
    das dekodierte Bild auf ``MOBILE_CAPTURE_MAX_DIMENSION`` (längste Seite)
    heruntergerechnet – der JPEG-Encode arbeitet so auf beschränktem Speicher.
    """
    from PIL import Image, UnidentifiedImageError

    _reject_if_pixel_bomb(raw)
    max_dim = _mobile_capture_max_dimension()
    try:
        with Image.open(io.BytesIO(raw)) as im:
            # JPEG: beim Dekodieren gleich vorskalieren (speichersparsam).
            im.draft("RGB", (max_dim, max_dim))
            rgb = im.convert("RGB")
            rgb.thumbnail((max_dim, max_dim))  # längste Seite <= max_dim
            out = io.BytesIO()
            rgb.save(out, format="JPEG", quality=90)
            return out.getvalue()
    except _InvalidImage:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise _InvalidImage(str(exc))


def _normalize_image_to_pdf_source(uploaded) -> bytes:
    """Liefert Bytes, die ``img2pdf`` sicher zu einer PDF-Seite macht.

    * HEIC/HEIF: ``pillow_heif`` **lazy** registrieren, dann via Pillow nach
      RGB-JPEG Q90 konvertieren.
    * JPEG/TIFF u. a.: zuerst direkt an ``img2pdf`` durchreichen (verlustfrei);
      lehnt ``img2pdf`` ab (z. B. PNG mit Alpha, CMYK-JPEG), defensiv über
      Pillow nach RGB-JPEG flatten.
    * Kein gültiges Bild → ``_InvalidImage`` (→ 400).
    """
    raw = uploaded.read()
    name = (getattr(uploaded, "name", "") or "").lower()
    content_type = (getattr(uploaded, "content_type", "") or "").lower()

    if name.endswith((".heic", ".heif")) or "heif" in content_type or "heic" in content_type:
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except Exception as exc:  # pragma: no cover - Dependency fehlt im Image
            raise _InvalidImage(f"HEIC-Unterstützung nicht verfügbar: {exc}")
        return _pillow_to_jpeg(raw)

    # Auch im verlustfreien Direkt-Pfad zuerst die Pixelzahl begrenzen: img2pdf
    # bettet das Bild unverändert ein, aber der spätere OCR-Schritt rastert es.
    _reject_if_pixel_bomb(raw)
    try:
        # Validiert Format und akzeptiert JPEG/TIFF verlustfrei.
        img2pdf.convert([raw])
        return raw
    except Exception:
        # PNG mit Alpha, CMYK-JPEG etc. → über Pillow flatten (oder _InvalidImage).
        return _pillow_to_jpeg(raw)


class MobileCaptureUploadView(APIView):
    """Mobile-Erfassung: mehrere Bilder (auch HEIC) → ein PDF → Pipeline.

    Multipart-Feld ``images`` (mehrfach, in Reihenfolge = Seitenreihenfolge),
    optional ``title``. ``owner`` = eingeloggter Nutzer, ``ingest_source`` =
    ``"mobile"``. OCR/Hash-Kette laufen anschließend asynchron im Worker.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [CaptureRateThrottle]  # DoS-Schutz (P1)

    MAX_IMAGES = 30
    MAX_BYTES_PER_IMAGE = 25 * 1024 * 1024  # ~25 MB

    @property
    def MAX_TOTAL_BYTES(self) -> int:
        # Deckel über ALLE Bilder zusammen (nicht nur je Bild): 30x25 MB = 750 MB
        # wären sonst zulässig und würden – gleichzeitig gehalten + dekodiert – den
        # Pod per RAM töten. Default 120 MB, per Env justierbar.
        return int(getattr(settings, "MOBILE_CAPTURE_MAX_TOTAL_BYTES", 120 * 1024 * 1024))

    def post(self, request):
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        images = request.FILES.getlist("images")
        if not images:
            return Response(
                {"detail": "Feld 'images' fehlt."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(images) > self.MAX_IMAGES:
            return Response(
                {"detail": f"Zu viele Bilder – maximal {self.MAX_IMAGES} erlaubt."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        total = sum(int(getattr(u, "size", 0) or 0) for u in images)
        if total > self.MAX_TOTAL_BYTES:
            mb = self.MAX_TOTAL_BYTES // (1024 * 1024)
            return Response(
                {"detail": f"Bilder überschreiten das Gesamtlimit (max. {mb} MB zusammen)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Jede normalisierte Seite SOFORT auf die Platte schreiben (nicht alle Bytes
        # gleichzeitig im RAM halten) und die PDF-Ausgabe direkt in eine Temp-Datei
        # streamen (img2pdf ``outputstream``) – der Web-Prozess hält nie mehr als ein
        # Bild + Puffer im Speicher. Temp-Dateien werden am Ende garantiert entfernt.
        tmp_dir = tempfile.mkdtemp(prefix="dms-mobile-")
        try:
            page_paths: list[str] = []
            for idx, uploaded in enumerate(images):
                if uploaded.size and uploaded.size > self.MAX_BYTES_PER_IMAGE:
                    return Response(
                        {"detail": f"Datei {uploaded.name} ist zu groß (max. 25 MB je Bild)."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                try:
                    page_bytes = _normalize_image_to_pdf_source(uploaded)
                except _InvalidImage:
                    return Response(
                        {"detail": f"Datei {uploaded.name} ist kein gültiges Bild."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                page_path = os.path.join(tmp_dir, f"page-{idx:04d}")
                with open(page_path, "wb") as fh:
                    fh.write(page_bytes)
                page_paths.append(page_path)
                del page_bytes  # Referenz freigeben (GC), bevor das nächste Bild kommt

            title = request.data.get("title") or (
                f"Mobile-Erfassung {timezone.localdate().strftime('%d.%m.%Y')}"
            )
            safe_title = slugify(title) or "mobile-erfassung"

            # PDF direkt in eine Temp-Datei streamen (Reihenfolge = Request-Reihenfolge).
            pdf_path = os.path.join(tmp_dir, f"{safe_title}.pdf")
            with open(pdf_path, "wb") as out:
                img2pdf.convert(page_paths, outputstream=out)

            with open(pdf_path, "rb") as pdf_fh:
                # storage.save_upload streamt via .chunks() – kein Voll-Read in den RAM.
                django_file = File(pdf_fh, name=f"{safe_title}.pdf")
                file_path, size, _mime = storage.save_upload(django_file)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        document, version = pipeline.create_document_from_file(
            file_path,
            title=title,
            owner=request.user,
            mime="application/pdf",
            size=size,
            ingest_source="mobile",
        )
        # OCR/Hash-Kette asynchron im Celery-Worker.
        _enqueue_processing(version.id)

        return Response(
            DocumentSerializer(document).data,
            status=status.HTTP_201_CREATED,
        )


def _harden_file_response(response):
    """Setzt Schutz-Header gegen Stored XSS auf einer Datei-Antwort (P0-2).

    * ``X-Content-Type-Options: nosniff`` – der Browser darf den deklarierten
      ``Content-Type`` nicht überstimmen (kein „HTML-Sniffing" einer als Bild
      deklarierten Datei).
    * ``Content-Security-Policy: sandbox`` – die ausgelieferte Datei wird in
      einen opaken Origin ohne Skriptausführung gezwungen; selbst wenn HTML/SVG
      durchrutschte, kann es nicht auf ``localStorage``/Cookies des DMS-Origins
      zugreifen.
    """
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox"
    return response


def _serve_version_preview(version):
    """Liefert das Archiv-PDF (Original als Fallback) einer Version inline.

    Der Pfad stammt ausschließlich aus der DB (nie aus Nutzereingaben) –
    keine Traversal-Gefahr. Gemeinsam genutzt von ``DocumentViewSet.preview``
    und den Freigabe-Abrufrouten (STOAA-191), damit beide Pfade identisch
    liefern.

    Sicherheit (P0): Der Content-Type wird aus den **Magic Bytes** der Datei
    bestimmt, NICHT aus dem gespeicherten ``mime_type`` (der falsch/leer sein
    kann). Nur erkannte, inline-sichere Typen (PDF/Raster-Bild) werden
    ausgeliefert; unerkannter oder aktiver Inhalt → 415. Damit wird ein
    ``%PDF-…<script>``-Polyglot als ``application/pdf`` serviert (nativer
    PDF-Viewer, KEIN HTML) statt als ``text/html`` – das Frontend macht aus der
    Antwort eine same-origin Blob-URL, die im (un-sandboxed) iframe rendert;
    ein text/html-Blob würde dort im DMS-Origin ausgeführt (Stored XSS).
    """
    path = version.archive_path or version.file_path
    if not path or not os.path.exists(path):
        raise Http404("Datei nicht gefunden.")
    # Archiv-PDF ist per Konstruktion ein von uns erzeugtes PDF; das Original
    # wird am Byte-Header verifiziert (nie am gespeicherten mime_type).
    if version.archive_path:
        content_type = "application/pdf"
    else:
        with open(path, "rb") as fh:
            info = detect(fh.read(SNIFF_BYTES))
        if info is None or not is_safe_inline(info.mime):
            return HttpResponse(
                "Vorschau für diesen Dateityp nicht verfügbar.",
                status=415,
                content_type="text/plain; charset=utf-8",
            )
        content_type = info.mime
    response = FileResponse(
        open(path, "rb"),
        content_type=content_type,
        as_attachment=False,
    )
    return _harden_file_response(response)


def _serve_version_download(document, version):
    """Lädt die Originaldatei (``file_path``) einer Version als Attachment.

    Bewusst das Original – über genau diese Bytes wird der ``sha256`` gebildet,
    sie sind damit verifizierbar. Gemeinsam genutzt von
    ``DocumentViewSet.download`` und den Freigabe-Abrufrouten (STOAA-191).
    """
    path = version.file_path
    if not path or not os.path.exists(path):
        raise Http404("Datei nicht gefunden.")
    filename = f"{document.title}-v{version.version_no}{os.path.splitext(path)[1]}"
    response = FileResponse(
        open(path, "rb"),
        as_attachment=True,
        filename=filename,
        content_type=version.mime_type or "application/octet-stream",
    )
    return _harden_file_response(response)


class DocumentViewSet(viewsets.ModelViewSet):
    """Dokumente auflisten/abrufen inkl. Volltextsuche und Filtern.

    Query-Parameter der Liste:
      * ``q``             – Gewichtete Volltextsuche (PostgreSQL FTS) über Titel,
                            Korrespondent, Dokumenttyp, Schlagworte, Mail-Betreff/
                            -Absender und OCR-Text. Gewichte: A=Titel+Korrespondent,
                            B=Dokumenttyp/Tags/Mail-Felder, D=OCR-Text (niedrigste
                            Priorität) → Treffer im Titel ranken vor OCR-Fließtext.
                            Kurze Queries (1–2 Zeichen) fallen auf ``icontains`` über
                            dieselben Felder zurück (FTS-Lexeme greifen dort schlecht).
      * ``correspondent`` – Filter auf Korrespondenten-ID
      * ``document_type`` – Filter auf Dokumenttyp-ID
      * ``storage_path``  – Filter auf Speicherpfad-ID
      * ``folder``        – Filter auf Ordner-ID; ``none`` zeigt Dokumente ohne Ordner
      * ``tag``           – Filter auf Tag-ID (mehrfach angebbar → ODER-Verknüpfung,
                            z. B. ``?tag=1&tag=2``)
      * ``ordering``      – Sortierung, z. B. ``added_at``/``-added_at`` (Datum)
                            oder ``title``/``-title`` (A–Z). Ohne Angabe gilt die
                            Standard-Sortierung (bei ``q`` nach FTS-Relevanz,
                            sonst ``-added_at`` aus ``Document.Meta.ordering``).
      * ``review_status`` – Fachlicher Inbox-Status: ``needs_review``/``reviewed``.
    """

    serializer_class = DocumentSerializer
    queryset = Document.objects.all()  # für Basename-Ableitung im Router
    permission_classes = [ReadOnlyOrCanWrite]
    # Nur der explizite ``ordering``-Param sortiert um; ohne Param bleibt die
    # Reihenfolge aus get_queryset() erhalten (FTS-Rang bei ``q``, sonst
    # Meta.ordering). Kein view-weites ``ordering``-Default, damit die
    # Relevanz-Sortierung der Volltextsuche nicht überschrieben wird.
    filter_backends = [OrderingFilter]
    ordering_fields = ["added_at", "title"]

    # Familien-Freigabe: NUR diese (lesenden) Actions erweitern die Sichtbarkeit auf
    # haushaltsgeteilte Fremd-Dokumente. Alles andere – Schreiben, mutierende
    # Sub-Actions und jede nicht gelistete Action – bleibt strikt owner-only.
    # Fail-closed: vergisst man hier eine Action, ist sie zu streng (kein Leak),
    # niemals zu offen.
    SAFE_READ_ACTIONS = frozenset(
        {
            "list",
            "retrieve",
            "preview",
            "download",
            "thumbnail",
            "similar",
            "briefing",
            "duplicates",
            "filing_suggestions",
            "audit",
            "quality",
            "evidence",
            "integrity",
            "qr",
            "by_asn",
            "revision_package",
        }
    )

    def get_queryset(self):
        qs = (
            Document.objects.all()
            .select_related(
                "correspondent",
                "document_type",
                "storage_path",
                "folder",
                # Ordner-Eltern vorladen: ``folder.full_path`` läuft die Eltern-
                # Kette hoch (Perf: sonst 1 Query je Ebene je Dokument). Zwei
                # Ebenen decken die übliche Verschachtelung ab; tiefer greift der
                # Fallback (selten).
                "folder__parent",
                "folder__parent__parent",
                "case_file",
                "current_version",
                # owner_username/is_owner + superseded_by_title sonst N+1 je Doku.
                "owner",
                "superseded_by",
            )
            .prefetch_related(
                "tags",
                "custom_field_values__field",
                "review_tasks",
            )
            # supersedes_count via Subquery statt .count() je Doku (N+1). BEWUSST
            # als Subquery, NICHT als Count()-Aggregat: Ein Aggregat erzwingt ein
            # GROUP BY, das die Default-Sortierung (-added_at) kippt. Die Subquery
            # bleibt eine einzige Query und lässt Ordering unberührt.
            .annotate(
                supersedes_count_ann=Coalesce(
                    Subquery(
                        Document.objects.filter(superseded_by=OuterRef("pk"))
                        .order_by()
                        .values("superseded_by")
                        .annotate(n=Count("pk"))
                        .values("n"),
                        output_field=IntegerField(),
                    ),
                    0,
                )
            )
        )
        # --- Owner-Isolation (STOAA-7) + Familien-Freigabe -----------------
        # Grundregel: Jeder Nutzer VERWALTET ausschließlich eigene Dokumente. Da
        # get_object() dieses Queryset nutzt, bleiben Update/Delete und alle
        # mutierenden Sub-Actions auf eigene Dokumente beschränkt (fremde IDs →
        # 404). NUR für lesende Actions (SAFE_READ_ACTIONS) wird die Sichtbarkeit
        # zusätzlich auf haushaltsgeteilte Fremd-Dokumente erweitert – Schreiben
        # darauf ist ausgeschlossen. Ausnahme: DMS-Admin verwaltet alles.
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            if self.action in self.SAFE_READ_ACTIONS:
                qs = qs.filter(_household_visibility_q(user))
            else:
                qs = qs.filter(owner=user)

        params = self.request.query_params

        # Soft-Merge: als Dublette ausgeblendete Dokumente aus der LISTE entfernen
        # (nur ``action == "list"`` – Detail/Actions bleiben erreichbar, sonst wäre
        # das Undo unmöglich). ``?include_superseded=1`` zeigt sie bei Bedarf doch.
        if self.action == "list" and not params.get("include_superseded"):
            qs = qs.exclude(superseded_by__isnull=False)

        # Familien-Freigabe-Ansicht (?shared=with-me|by-me). Baut auf der bereits
        # angewandten Lese-Sichtbarkeit auf: "with-me" blendet die eigenen
        # Dokumente aus (übrig bleiben die vom Haushalt an mich geteilten);
        # "by-me" zeigt meine eigenen, die ich per Dokument ODER Ordner freigegeben
        # habe. Nur in der Liste wirksam.
        if self.action == "list":
            shared_scope = params.get("shared")
            if shared_scope == "with-me":
                qs = qs.exclude(owner=user)
            elif shared_scope == "by-me":
                # Ordner, die der Nutzer selbst (in der Kette) freigegeben hat.
                by_me_folders = [
                    fid for fid, owners in _folder_share_map().items() if user.id in owners
                ]
                qs = qs.filter(owner=user).filter(
                    Q(shared_with_household=True) | Q(folder_id__in=by_me_folders)
                )

        # Triage-Ansicht (STOAA-295): Admins können mit ``?owner=none`` gezielt
        # die eigentümerlosen (Triage-)Dokumente auflisten und anschließend per
        # ``set-owner`` zuweisen. Für Nicht-Admins ist der Param wirkungslos –
        # ihr Queryset ist bereits auf ``owner=user`` isoliert (STOAA-7), ein
        # Leak in fremde/eigentümerlose Dokumente ist damit ausgeschlossen.
        if getattr(user, "is_dms_admin", False) and params.get("owner") == "none":
            qs = qs.filter(owner__isnull=True)

        q = params.get("q", "").strip()
        # ASN-Suche (STOAA-284/285): Eine Eingabe, die ausschließlich eine ASN ist
        # (``ASN12345`` oder die reine Nummer ``12345``), liefert exakt das
        # zugehörige Dokument – beide Formen sind äquivalent. Owner-Scope bleibt
        # gewahrt (qs ist bereits gefiltert). Gemischte Anfragen laufen weiter
        # über die Volltextsuche.
        if q and _ASN_QUERY_RE.match(q):
            asn_value = asn_service.coerce_asn(q)
            if asn_value is not None:
                asn_qs = qs.filter(asn=asn_value)
                # Nur wenn tatsächlich ein (eigenes) Dokument mit dieser ASN
                # existiert, wird exakt danach aufgelöst. Sonst fällt die reine
                # Zahl auf die normale Volltextsuche zurück (z. B. Jahreszahlen).
                if asn_qs.exists():
                    return asn_qs.distinct()
        if 0 < len(q) < 3:
            # Kurze Query: FTS-Lexeme greifen bei 1–2 Zeichen schlecht →
            # icontains-ODER über dieselben Felder. Kein ``rank`` → es gilt das
            # Standard-Ordering (Meta.ordering bzw. ``?ordering=``).
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
            from django.contrib.postgres.search import SearchQuery, SearchRank

            # Indexgestützte Volltextsuche (Perf, P2 Teil 5b): gegen den
            # materialisierten ``search_vector`` (GIN-Index
            # ``documents_search_vector_gin``) statt den Vektor je Anfrage über
            # Join-Tabellen neu zu bauen. Die Gewichte (Titel/Korrespondent = A,
            # Typ/Tags/Mail/Notiz = B, OCR = D) stecken bereits in der Spalte
            # (siehe services/search_vector.py; dort gepflegt via Signale +
            # Pipeline-Hook, Bestand per Backfill/Daten-Migration).
            # ``filter(search_vector=query)`` nutzt den GIN-Index (``@@``),
            # ``SearchRank`` rankt auf der gespeicherten Spalte.
            # Known-Limitation E-Mail-Adressen unverändert (s. search_vector.py).
            query = SearchQuery(q, config="german")
            qs = (
                qs.annotate(rank=SearchRank(F("search_vector"), query))
                .filter(search_vector=query)
                .order_by("-rank", "-added_at")
            )
            # Ergebnis-Snippet (STOAA-368/370): ts_headline über den OCR-Text der
            # aktuellen Version, gleiche Config/Query-Form wie oben. Als
            # Query-Annotation wird es nur für die tatsächlich gelesenen Zeilen
            # der Ergebnisseite (Pagination) berechnet. Der Serializer sanitized
            # den Rohtext (Sentinels → <mark>, Rest escaped) zu ``snippet``.
            from .services.search_snippet import headline_annotation

            qs = qs.annotate(snippet_raw=headline_annotation(q))

        if params.get("correspondent"):
            qs = qs.filter(correspondent_id=params["correspondent"])
        if params.get("document_type"):
            qs = qs.filter(document_type_id=params["document_type"])
        if params.get("storage_path"):
            qs = qs.filter(storage_path_id=params["storage_path"])
        if params.get("case_file"):
            qs = qs.filter(case_file_id=params["case_file"])
        folder = params.get("folder")
        if folder == "none":
            qs = qs.filter(folder__isnull=True)
        elif folder:
            qs = qs.filter(folder_id=folder)
        review_status = params.get("review_status")
        if review_status in {choice for choice, _label in Document.ReviewStatus.choices}:
            qs = qs.filter(review_status=review_status)
        # Verarbeitungsstatus-Filter (STOAA-248): grobe UI-Buckets auf den
        # ``processing_state`` der aktuellen Version. Bewusst manuell (kein
        # django-filter). ``processing`` fasst alle In-Flight-States zusammen;
        # ``failed``/``retry_pending``/``ready`` sind eigene Buckets. Ein Wert,
        # der kein Bucket ist, wird als exakter State interpretiert; ein
        # unbekannter Wert wird ignoriert (kein 500, kein Filter).
        processing_state = params.get("processing_state")
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
                # Kein Bucket, aber ein gültiger exakter State.
                states = [processing_state]
            if states is not None:
                qs = qs.filter(current_version__processing_state__in=states)
        # ``tag`` mehrfach erlaubt (?tag=1&tag=2) → ODER via ``__in``;
        # ein einzelner Wert bleibt abwärtskompatibel (getlist → ["1"]).
        tags = params.getlist("tag")
        if tags:
            qs = qs.filter(tags__id__in=tags)

        # Zusatzfeld-Bereichsfilter (Spec §7.3): custom_field_<id>_gte/_lte
        qs = _apply_custom_field_filters(qs, params)

        # Die vollständige Versionshistorie NUR laden, wenn sie auch serialisiert
        # wird (Detail/Einzel-Serializer). Die Liste (DocumentListSerializer) kommt
        # ohne aus – sonst lädt jede Listenseite alle Versionen jedes Dokuments.
        if self.action != "list":
            qs = qs.prefetch_related("versions", "versions__created_by")

        return qs.distinct()

    def get_serializer_class(self):
        # Liste: schlanker Serializer ohne nested ``versions`` (Perf/Payload).
        # Detail & alle übrigen Aktionen: voller DocumentSerializer inkl. Historie.
        if self.action == "list":
            return DocumentListSerializer
        return DocumentSerializer

    def _resolve_version(self, document):
        """Wählt die Version aus ``?version=<nr>`` oder fällt auf die aktuelle zurück.

        Die Nummer wird gegen die DB validiert (kein Nutzerpfad) – keine Traversal-Gefahr.
        """
        raw = self.request.query_params.get("version")
        if raw:
            try:
                version_no = int(raw)
            except (TypeError, ValueError):
                raise Http404("Ungültige Versionsnummer.")
            version = document.versions.filter(version_no=version_no).first()
            if version is None:
                raise Http404("Version nicht vorhanden.")
            return version
        return document.current_version
    def perform_create(self, serializer):
        """Setzt den Eigentümer serverseitig – nie aus dem Request übernehmbar.

        Verhindert, dass ein Nutzer beim Anlegen ein fremdes ``owner`` setzt.
        (Der reguläre Upload-Pfad setzt owner ohnehin; hier für direktes POST.)
        """
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        """Liefert das Archiv-PDF einer Version zur Inline-Vorschau.

        Standard ist die aktuelle Version; ``?version=<nr>`` wählt eine ältere.
        Fällt auf das Original zurück, falls (noch) kein OCR-Archiv existiert.
        Der Pfad stammt aus der DB (nicht aus Nutzereingaben) – keine Traversal-Gefahr.
        """
        document = self.get_object()
        version = self._resolve_version(document)
        if version is None:
            raise Http404("Keine Version vorhanden.")
        return _serve_version_preview(version)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """Lädt die Originaldatei einer Version herunter (Basis der Hash-Prüfung).

        Standard ist die aktuelle Version; ``?version=<nr>`` wählt eine ältere.
        Es wird bewusst das Original (``file_path``) geliefert – über genau diese
        Bytes wird der ``sha256`` gebildet, sie sind damit verifizierbar.
        """
        document = self.get_object()
        version = self._resolve_version(document)
        if version is None:
            raise Http404("Keine Version vorhanden.")
        return _serve_version_download(document, version)

    @action(detail=True, methods=["get"], url_path="revision-package")
    def revision_package(self, request, pk=None):
        """Exportiert ein prüfbares ZIP-Paket für Steuer/Anwalt/Behörde."""
        document = self.get_object()
        AuditLogEntry.objects.create(
            actor=request.user,
            action="revision_package_export",
            object_type="Document",
            object_id=str(document.id),
            detail={"format": "zip", "scope": "document"},
        )
        package = revision_package_service.build_document_revision_package(document)
        # Streamen statt im RAM halten: Datei öffnen und sofort entlinken – auf
        # POSIX bleibt sie über den offenen fd lesbar und wird beim Schließen der
        # Response (Ende des Streamings) automatisch freigegeben. Kein Vollkopieren
        # in den Web-Prozess, kein Temp-Leak.
        handle = open(package.path, "rb")
        try:
            os.unlink(package.path)
        except OSError:
            pass
        response = FileResponse(
            handle,
            as_attachment=True,
            filename=package.filename,
            content_type="application/zip",
        )
        return response

    @action(detail=False, methods=["get"], url_path="evidence-status")
    def evidence_status(self, request):
        """Mandantengefiltertes Audit-/Beweis-Center für sichtbare Dokumente."""
        return Response(evidence_service.evidence_status(self.get_queryset()))

    @action(detail=False, methods=["get"], url_path="quality-status")
    def quality_status(self, request):
        """Mandantengefiltertes Qualitätscenter für sichtbare Dokumente.

        Perf (#6): Das Center scored bei jedem Aufruf ALLE sichtbaren Dokumente
        in Python (Heuristiken – keine DB-Aggregation möglich). Da das Ergebnis
        nur für Sekunden „frisch" sein muss, wird es kurz gecacht (Default 60 s,
        ``QUALITY_STATUS_CACHE_TTL``; 0 = aus). Key = Nutzer + Query-String, damit
        Owner-Scope und Filter sauber getrennt bleiben.
        """
        from django.core.cache import cache

        ttl = int(getattr(settings, "QUALITY_STATUS_CACHE_TTL", 60))
        if ttl <= 0:
            return Response(quality_service.quality_status(self.get_queryset()))

        raw_key = f"{request.user.id}?{request.META.get('QUERY_STRING', '')}"
        cache_key = "quality_status:" + hashlib.sha256(raw_key.encode()).hexdigest()
        data = cache.get(cache_key)
        if data is None:
            data = quality_service.quality_status(self.get_queryset())
            cache.set(cache_key, data, ttl)
        return Response(data)

    @action(detail=True, methods=["get"], url_path="quality")
    def quality(self, request, pk=None):
        """Qualitätsprofil eines einzelnen sichtbaren Dokuments."""
        return Response(quality_service.document_quality(self.get_object()))

    @action(detail=True, methods=["get"], url_path="evidence")
    def evidence(self, request, pk=None):
        """Frisch verifizierter Beweisbericht für ein einzelnes Dokument."""
        document = self.get_object()
        AuditLogEntry.objects.create(
            actor=request.user,
            action="evidence_report_view",
            object_type="Document",
            object_id=str(document.id),
            detail={"scope": "document"},
        )
        return Response(evidence_service.document_report(document))

    @action(detail=True, methods=["get"])
    def integrity(self, request, pk=None):
        """Prüft die Hash-Kette des Dokuments (Datei-Hash + prev_hash-Verkettung).

        Nur-Lesen – auch für Gäste. Rechnet die Datei-Hashes frisch nach.
        """
        document = self.get_object()
        return Response(pipeline.verify_document_integrity(document))

    @action(detail=True, methods=["post"], url_path="archive-check")
    def archive_check(self, request, pk=None):
        """Persistente Archivprüfung: Hash-Kette + Metadaten-Siegel + WORM."""
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        report = archive_service.verify_document_archive(document)
        AuditLogEntry.objects.create(
            actor=request.user,
            action="archive_check",
            object_type="Document",
            object_id=str(document.id),
            detail={
                "status": report["status"],
                "errors": report["errors"],
                "warnings": report["warnings"],
            },
        )
        return Response(report)

    @action(detail=True, methods=["post"], url_path="legal-hold")
    def legal_hold(self, request, pk=None):
        """Setzt oder entfernt Legal Hold für ein Dokument."""
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        enabled = bool(request.data.get("enabled", True))
        reason = str(request.data.get("reason", "")).strip()
        if enabled and not reason:
            return Response(
                {"detail": "Für Legal Hold ist eine Begründung erforderlich."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old = document.legal_hold
        document.legal_hold = enabled
        document.legal_hold_reason = reason if enabled else ""
        document.legal_hold_set_at = timezone.now() if enabled else None
        document.legal_hold_set_by = request.user if enabled else None
        document.save(
            update_fields=[
                "legal_hold",
                "legal_hold_reason",
                "legal_hold_set_at",
                "legal_hold_set_by",
            ]
        )
        AuditLogEntry.objects.create(
            actor=request.user,
            action="legal_hold",
            object_type="Document",
            object_id=str(document.id),
            detail={"from": old, "to": enabled, "reason": reason},
        )
        return Response(self.get_serializer(document).data)

    @action(detail=True, methods=["get"], url_path="similar")
    def similar(self, request, pk=None):
        """Liefert semantisch ähnliche sichtbare Dokumente."""
        document = self.get_object()
        try:
            limit = int(request.query_params.get("limit", 6))
        except (TypeError, ValueError):
            limit = 6
        limit = max(1, min(limit, 20))
        results = semantic_index_service.similar_documents(
            document,
            self.get_queryset(),
            limit=limit,
        )
        indexed = bool(
            document.current_version
            and document.current_version.chunks.filter(
                embedding__isnull=False
            ).exists()
        )
        return Response(
            {
                "document": document.id,
                "indexed": indexed,
                "model": semantic_index_service.EMBEDDING_MODEL,
                "results": results,
            }
        )

    @action(detail=True, methods=["get"], url_path="briefing")
    def briefing(self, request, pk=None):
        """Handlungsorientiertes Dokument-Briefing aus vorhandenen DMS-Signalen."""
        document = self.get_object()
        payload = document_briefing_service.build_document_briefing(
            document,
            visible_documents=self.get_queryset(),
        )
        return Response(payload)

    @action(detail=True, methods=["post"], url_path="reindex-semantic")
    def reindex_semantic(self, request, pk=None):
        """Erzeugt den semantischen Index für dieses Dokument neu."""
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        result = semantic_index_service.sync_document_embeddings(document)
        AuditLogEntry.objects.create(
            actor=request.user,
            action="semantic_reindex",
            object_type="Document",
            object_id=str(document.id),
            detail=result,
        )
        return Response(result)

    @action(detail=False, methods=["get"], url_path=r"by-asn/(?P<asn>[^/]+)")
    def by_asn(self, request, asn=None):
        """Liefert das Dokument zu einer ASN (``GET /api/documents/by-asn/{asn}``).

        Akzeptiert sowohl ``ASN000123`` als auch die reine Nummer ``123``. Owner-
        Scoping über ``get_queryset()`` – ein fremdes/unbekanntes Dokument ergibt
        404 (kein Leak). Nur-Lesen. Die ASN-Auflösung liegt vollständig im Service
        (keine ASN-Logik im ViewSet).
        """
        asn_value = asn_service.coerce_asn(asn)
        if asn_value is None:
            raise Http404("Ungültige ASN.")
        document = self.get_queryset().filter(asn=asn_value).first()
        if document is None:
            raise Http404("Kein Dokument mit dieser ASN gefunden.")
        return Response(self.get_serializer(document).data)

    @action(detail=True, methods=["get"])
    def qr(self, request, pk=None):
        """Liefert den QR-Code des Dokuments als PNG (``GET /api/documents/{id}/qr``).

        Der Code enthält ausschließlich die ASN (``ASN000123``) – keine URL, kein
        JSON. Die Erzeugung liegt vollständig im Service ``asn.render_qr``. Nur-
        Lesen; Owner-Scoping über ``get_object()``.
        """
        document = self.get_object()
        if not document.asn:
            # Sticker-only: ohne erkannten Barcode/QR hat das Dokument keine ASN.
            return Response(
                {"detail": "Dokument hat keine ASN (noch kein Barcode/QR erkannt)."},
                status=status.HTTP_404_NOT_FOUND,
            )
        png = asn_service.render_qr(document)
        response = HttpResponse(png, content_type="image/png")
        response["Content-Disposition"] = (
            f'inline; filename="{asn_service.format_asn(document.asn)}.png"'
        )
        return response

    @action(
        detail=True,
        methods=["post"],
        parser_classes=[MultiPartParser, FormParser],
        throttle_classes=[UploadRateThrottle],  # DoS-Schutz (P1)
    )
    def add_version(self, request, pk=None):
        """Hängt eine neue Datei als nächste Version an das bestehende Dokument.

        Feld: ``file`` (Pflicht). OCR/Hash-Kette laufen asynchron im Worker.
        Schreiben nur für ``can_write`` (Gäste nicht).
        """
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self.get_object()
        uploaded = request.FILES.get("file")
        if uploaded is None:
            return Response(
                {"detail": "Feld 'file' fehlt."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            file_path, size, mime = storage.save_upload(uploaded)
        except UnsupportedFileType as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        version = pipeline.create_version_for_document(
            document, file_path, created_by=request.user, mime=mime, size=size
        )
        # OCR/Hash-Kette asynchron im Celery-Worker.
        _enqueue_processing(version.id)

        document.refresh_from_db()
        return Response(
            self.get_serializer(document).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def thumbnail(self, request, pk=None):
        """Liefert das Miniaturbild der ersten Seite (JPEG).

        Erzeugt es bei Bedarf lazy (für Dokumente aus der Zeit vor dieser Funktion).
        """
        document = self.get_object()
        version = document.current_version
        if version is None:
            raise Http404("Keine Version vorhanden.")

        path = version.thumbnail_path
        if not path or not os.path.exists(path):
            path = pipeline.generate_thumbnail(version)
        if not path or not os.path.exists(path):
            raise Http404("Keine Vorschau verfügbar.")

        # HTTP-Caching (Perf): Ohne Validator lud jede Kartenansicht den JPEG-Blob
        # neu. Der ETag identifiziert Version (id + Inhalts-Hash) und die konkrete
        # Thumbnail-Datei (mtime/size); ändert sich die aktuelle Version, ändert
        # sich der ETag. Bei passendem If-None-Match antworten wir mit 304 (kein
        # Blob). Cache-Control ``private`` (auth-geschützt) mit kurzer Frische.
        st = os.stat(path)
        raw = f"{version.id}:{version.sha256}:{int(st.st_mtime)}:{st.st_size}"
        etag = '"' + hashlib.md5(raw.encode()).hexdigest() + '"'
        if request.headers.get("If-None-Match") == etag:
            response = HttpResponseNotModified()
            response["ETag"] = etag
            response["Cache-Control"] = "private, max-age=3600"
            return response

        response = FileResponse(open(path, "rb"), content_type="image/jpeg")
        response["ETag"] = etag
        response["Cache-Control"] = "private, max-age=3600"
        return response

    @action(detail=True, methods=["get"])
    def audit(self, request, pk=None):
        """Lückenloses, chronologisches Protokoll dieses Dokuments (paginiert).

        Enthält Dokument-Ereignisse (Upload, Metadaten-Änderung, Klassifizierung,
        Löschung) sowie zugehörige Versions-Ereignisse (z. B. OCR). Wer das Dokument
        sehen darf, darf den Verlauf lesen; Schreibrechte sind nicht nötig (GET).
        """
        document = self.get_object()
        version_ids = [str(v.id) for v in document.versions.all()]
        entries = (
            AuditLogEntry.objects.filter(
                Q(object_type="Document", object_id=str(document.id))
                | Q(object_type="DocumentVersion", object_id__in=version_ids)
            )
            .select_related("actor")
            .order_by("-timestamp", "-id")
        )

        page = self.paginate_queryset(entries)
        serializer = AuditLogEntrySerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=True, methods=["get", "post"], url_path="extraction-candidates")
    def extraction_candidates(self, request, pk=None):
        """Smart-Inbox-Kandidaten listen oder für ein Dokument neu erzeugen.

        GET ist lesend und owner-gescoped. POST ist ein Schreibvorgang, weil
        neue Kandidaten persistiert werden; Gäste erhalten 403 über Permission
        + expliziten Guard.
        """
        document = self.get_object()
        if request.method == "POST":
            if not request.user.can_write:
                return Response(
                    {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            from .services import extraction

            created = extraction.generate_candidates(document)
            AuditLogEntry.objects.create(
                actor=request.user,
                action="generate_extraction_candidates",
                object_type="Document",
                object_id=str(document.id),
                detail={"created": created},
            )

        candidates = document.extraction_candidates.order_by(
            "status", "field", "-confidence", "source_page"
        )
        return Response(ExtractionCandidateSerializer(candidates, many=True).data)

    def _get_candidate(self, document, candidate_id):
        candidate = document.extraction_candidates.filter(pk=candidate_id).first()
        if candidate is None:
            raise Http404("Extraktionsvorschlag nicht vorhanden.")
        return candidate

    def _apply_candidate_value(self, document, candidate):
        """Schreibt einen übernommenen Kandidaten auf das passende Zielfeld."""
        value = candidate.normalized_value or candidate.value
        if candidate.field == ExtractionCandidate.Field.DOCUMENT_DATE:
            parsed = _parse_iso_date(value)
            if parsed is None:
                return None
            document.created_at = parsed
            document.save(update_fields=["created_at"])
            return {"document_field": "created_at", "value": value}

        target = _EXTRACTION_CUSTOM_FIELD_TARGETS.get(candidate.field)
        if target is None:
            return None
        name, data_type = target
        custom_field, _created = CustomField.objects.get_or_create(
            name=name,
            defaults={"data_type": data_type},
        )
        CustomFieldValue.objects.update_or_create(
            document=document,
            field=custom_field,
            defaults={"value": value},
        )
        return {
            "custom_field": custom_field.name,
            "custom_field_id": custom_field.id,
            "value": value,
        }

    @action(
        detail=True,
        methods=["post"],
        url_path=r"extraction-candidates/(?P<candidate_id>[0-9]+)/apply",
    )
    def apply_extraction_candidate(self, request, pk=None, candidate_id=None):
        """Übernimmt genau einen Smart-Inbox-Kandidaten."""
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        candidate = self._get_candidate(document, candidate_id)
        if candidate.status != ExtractionCandidate.Status.PENDING:
            return Response(
                {"detail": "Dieser Vorschlag ist nicht mehr offen."},
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            target = self._apply_candidate_value(document, candidate)
            if target is None:
                return Response(
                    {"detail": "Vorschlag konnte nicht übernommen werden."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            candidate.status = ExtractionCandidate.Status.APPLIED
            candidate.applied_at = timezone.now()
            candidate.save(update_fields=["status", "applied_at"])
            AuditLogEntry.objects.create(
                actor=request.user,
                action="apply_extraction_candidate",
                object_type="Document",
                object_id=str(document.id),
                detail={
                    "candidate": candidate.id,
                    "field": candidate.field,
                    "value": candidate.value,
                    "normalized_value": candidate.normalized_value,
                    "target": target,
                },
            )
            review_task_service.sync_document_review_tasks(document)

        return Response(ExtractionCandidateSerializer(candidate).data)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"extraction-candidates/(?P<candidate_id>[0-9]+)/dismiss",
    )
    def dismiss_extraction_candidate(self, request, pk=None, candidate_id=None):
        """Verwirft genau einen Smart-Inbox-Kandidaten ohne Dokumentänderung."""
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        candidate = self._get_candidate(document, candidate_id)
        if candidate.status != ExtractionCandidate.Status.PENDING:
            return Response(
                {"detail": "Dieser Vorschlag ist nicht mehr offen."},
                status=status.HTTP_409_CONFLICT,
            )
        candidate.status = ExtractionCandidate.Status.DISMISSED
        candidate.dismissed_at = timezone.now()
        candidate.save(update_fields=["status", "dismissed_at"])
        AuditLogEntry.objects.create(
            actor=request.user,
            action="dismiss_extraction_candidate",
            object_type="Document",
            object_id=str(document.id),
            detail={
                "candidate": candidate.id,
                "field": candidate.field,
                "value": candidate.value,
                "normalized_value": candidate.normalized_value,
            },
        )
        review_task_service.sync_document_review_tasks(document)
        return Response(ExtractionCandidateSerializer(candidate).data)

    @action(detail=True, methods=["get", "post"], url_path="case-candidates")
    def case_candidates(self, request, pk=None):
        """Akten-Autopilot-Kandidaten listen oder neu erzeugen."""
        document = self.get_object()
        if request.method == "POST":
            if not request.user.can_write:
                return Response(
                    {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            from .services import case_matching

            created = case_matching.generate_candidates(document)
            AuditLogEntry.objects.create(
                actor=request.user,
                action="generate_case_file_candidates",
                object_type="Document",
                object_id=str(document.id),
                detail={"created": created},
            )

        candidates = document.case_file_candidates.select_related("case_file").order_by(
            "status", "-score", "-created_at"
        )
        return Response(CaseFileCandidateSerializer(candidates, many=True).data)

    @action(detail=False, methods=["get"], url_path="inbox-candidates")
    def inbox_candidates(self, request):
        """Batch (#1): Extraction- + Case-Kandidaten mehrerer Dokumente in EINEM
        Request. Ersetzt den Pro-Dokument-Request-Storm der Smart-Inbox
        (früher 2 Requests je Dokument). Owner-Scope über ``get_queryset``.

        ``?ids=1,2,3`` → ``{"1": {"extraction": [...], "cases": [...]}, ...}``.
        Per ``Prefetch`` gilt exakt dieselbe Sortierung wie in den Einzel-
        Endpunkten, und die gesamte Antwort kostet nur ~3 Queries statt 2·N.
        """
        raw = request.query_params.get("ids", "")
        ids = [int(x) for x in raw.split(",") if x.strip().isdigit()][:200]
        if not ids:
            return Response({})

        documents = (
            self.get_queryset()
            .filter(pk__in=ids)
            .prefetch_related(
                Prefetch(
                    "extraction_candidates",
                    queryset=ExtractionCandidate.objects.order_by(
                        "status", "field", "-confidence", "source_page"
                    ),
                ),
                Prefetch(
                    "case_file_candidates",
                    queryset=CaseFileCandidate.objects.select_related(
                        "case_file"
                    ).order_by("status", "-score", "-created_at"),
                ),
            )
        )

        result: dict[str, dict] = {}
        for document in documents:
            result[str(document.id)] = {
                "extraction": ExtractionCandidateSerializer(
                    document.extraction_candidates.all(), many=True
                ).data,
                "cases": CaseFileCandidateSerializer(
                    document.case_file_candidates.all(), many=True
                ).data,
            }
        return Response(result)

    def _get_case_candidate(self, document, candidate_id):
        candidate = (
            document.case_file_candidates.select_related("case_file")
            .filter(pk=candidate_id)
            .first()
        )
        if candidate is None:
            raise Http404("Aktenvorschlag nicht vorhanden.")
        return candidate

    @action(
        detail=True,
        methods=["post"],
        url_path=r"case-candidates/(?P<candidate_id>[0-9]+)/apply",
    )
    def apply_case_candidate(self, request, pk=None, candidate_id=None):
        """Übernimmt genau einen Aktenvorschlag."""
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        candidate = self._get_case_candidate(document, candidate_id)
        if candidate.status != CaseFileCandidate.Status.PENDING:
            return Response(
                {"detail": "Dieser Aktenvorschlag ist nicht mehr offen."},
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            if candidate.kind == CaseFileCandidate.Kind.EXISTING_CASE:
                case_file = candidate.case_file
                if case_file is None:
                    return Response(
                        {"detail": "Bestehende Zielakte fehlt."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if (
                    not getattr(request.user, "is_dms_admin", False)
                    and case_file.owner_id != request.user.id
                ):
                    return Response(
                        {"detail": "Zielakte ist nicht sichtbar."},
                        status=status.HTTP_404_NOT_FOUND,
                    )
            else:
                case_file = CaseFile.objects.create(
                    title=candidate.suggested_title or document.title,
                    description=f"Aus Dokument #{document.id} vorgeschlagen.",
                    owner=document.owner or request.user,
                )
                candidate.case_file = case_file

            document.case_file = case_file
            document.save(update_fields=["case_file"])
            now = timezone.now()
            candidate.status = CaseFileCandidate.Status.APPLIED
            candidate.applied_at = now
            candidate.save(update_fields=["case_file", "status", "applied_at"])
            document.case_file_candidates.filter(
                status=CaseFileCandidate.Status.PENDING
            ).exclude(pk=candidate.pk).update(
                status=CaseFileCandidate.Status.DISMISSED,
                dismissed_at=now,
            )
            AuditLogEntry.objects.create(
                actor=request.user,
                action="apply_case_file_candidate",
                object_type="Document",
                object_id=str(document.id),
                detail={
                    "candidate": candidate.id,
                    "kind": candidate.kind,
                    "case_file": case_file.id,
                    "case_file_title": case_file.title,
                    "score": candidate.score,
                },
            )
            review_task_service.sync_document_review_tasks(document)

        return Response(CaseFileCandidateSerializer(candidate).data)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"case-candidates/(?P<candidate_id>[0-9]+)/dismiss",
    )
    def dismiss_case_candidate(self, request, pk=None, candidate_id=None):
        """Verwirft genau einen Aktenvorschlag ohne Dokumentänderung."""
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        candidate = self._get_case_candidate(document, candidate_id)
        if candidate.status != CaseFileCandidate.Status.PENDING:
            return Response(
                {"detail": "Dieser Aktenvorschlag ist nicht mehr offen."},
                status=status.HTTP_409_CONFLICT,
            )
        candidate.status = CaseFileCandidate.Status.DISMISSED
        candidate.dismissed_at = timezone.now()
        candidate.save(update_fields=["status", "dismissed_at"])
        AuditLogEntry.objects.create(
            actor=request.user,
            action="dismiss_case_file_candidate",
            object_type="Document",
            object_id=str(document.id),
            detail={
                "candidate": candidate.id,
                "kind": candidate.kind,
                "case_file": candidate.case_file_id,
                "suggested_title": candidate.suggested_title,
                "score": candidate.score,
            },
        )
        review_task_service.sync_document_review_tasks(document)
        return Response(CaseFileCandidateSerializer(candidate).data)

    def _metadata_snapshot(self, document) -> dict:
        """Menschlich lesbarer Schnappschuss der Metadaten für den Audit-Diff."""
        return {
            "title": document.title,
            "correspondent": (
                document.correspondent.name if document.correspondent_id else None
            ),
            "document_type": (
                document.document_type.name if document.document_type_id else None
            ),
            "storage_path": (
                document.storage_path.name if document.storage_path_id else None
            ),
            "folder": document.folder.full_path if document.folder_id else None,
            "tags": sorted(t.name for t in document.tags.all()),
        }

    def perform_update(self, serializer):
        """Speichert, protokolliert Metadaten-Änderungen und feuert Workflow-Engine."""
        before = self._metadata_snapshot(serializer.instance)
        super().perform_update(serializer)
        document = serializer.instance
        after = self._metadata_snapshot(document)
        changes = {
            field: {"from": before[field], "to": after[field]}
            for field in before
            if before[field] != after[field]
        }
        if changes:
            AuditLogEntry.objects.create(
                actor=self.request.user,
                action="update",
                object_type="Document",
                object_id=str(document.id),
                detail={"changes": changes},
            )
        # Workflow-Engine: document_updated
        from . import workflows
        workflows.run_workflows(document, trigger_type="document_updated", source="api")
        review_task_service.sync_document_review_tasks(document)

    def perform_destroy(self, instance):
        """Protokolliert die Löschung, bevor das Dokument entfernt wird.

        Audit-Einträge referenzieren die ID als String (keine FK) und überleben
        die Löschung des Dokuments – das Protokoll bleibt append-only lückenlos.
        """
        from rest_framework.exceptions import PermissionDenied

        if instance.legal_hold:
            AuditLogEntry.objects.create(
                actor=self.request.user,
                action="legal_hold_block",
                object_type="Document",
                object_id=str(instance.id),
                detail={"title": instance.title, "reason": instance.legal_hold_reason},
            )
            raise PermissionDenied(
                "Dokument steht unter Legal Hold und kann nicht gelöscht werden."
            )

        # WORM: Dokument mit unveränderlichen Versionen darf nicht gelöscht werden.
        if instance.versions.filter(is_immutable=True).exists():
            AuditLogEntry.objects.create(
                actor=self.request.user,
                action="immutable_block",
                object_type="Document",
                object_id=str(instance.id),
                detail={"title": instance.title, "reason": "unveränderliche Version vorhanden"},
            )
            raise PermissionDenied(
                "Dokument enthält unveränderliche Versionen und kann nicht gelöscht werden."
            )

        # Aufbewahrungsfrist: retention_until am Dokument prüfen.
        today = timezone.now().date()
        if instance.retention_until and today < instance.retention_until:
            AuditLogEntry.objects.create(
                actor=self.request.user,
                action="retention_block",
                object_type="Document",
                object_id=str(instance.id),
                detail={"title": instance.title, "retention_until": str(instance.retention_until)},
            )
            raise PermissionDenied(
                f"Aufbewahrungsfrist bis {instance.retention_until} aktiv – Löschen gesperrt."
            )

        AuditLogEntry.objects.create(
            actor=self.request.user,
            action="delete",
            object_type="Document",
            object_id=str(instance.id),
            detail={"title": instance.title},
        )
        super().perform_destroy(instance)

    def _parse_document_ids(self, raw_ids):
        """Normalisiert eine ID-Liste für Mailroom-/Bulk-Actions.

        Akzeptiert ausschließlich nicht-leere Listen. Duplikate werden stabil
        entfernt, damit die API-Antworten für Batch-Aktionen vorhersehbar
        bleiben und ein Dokument nicht doppelt auditierbar geändert wird.
        """
        if not isinstance(raw_ids, list) or not raw_ids:
            raise DjangoValidationError("Feld 'ids' muss eine nicht-leere Liste sein.")
        document_ids = []
        for raw_id in raw_ids:
            try:
                document_ids.append(int(raw_id))
            except (TypeError, ValueError) as exc:
                raise DjangoValidationError(
                    f"Ungültige Dokument-ID: {raw_id!r}."
                ) from exc
        return list(dict.fromkeys(document_ids))

    def _resolve_scoped_documents(self, requested_ids):
        """Liefert sichtbare Dokumente plus Teilfehler ohne Existenz-Leak."""
        scoped = list(self.get_queryset().filter(id__in=requested_ids))
        scoped_ids = {document.id for document in scoped}
        errors = [
            {"id": document_id, "error": "nicht gefunden oder keine Berechtigung"}
            for document_id in requested_ids
            if document_id not in scoped_ids
        ]
        return scoped, errors

    def _create_review_rule(self, document, match_text: str):
        """Erzeugt eine erklärbare Klassifizierungsregel aus bestätigten Metadaten.

        Das ist der Lernmodus der Inbox: Eine Nutzerkorrektur wird nur dann zu
        einer zukünftigen Automatik, wenn ein konkreter Match-Text angegeben ist.
        Gespeichert werden Namen statt IDs, damit Regeln lesbar, exportierbar und
        zwischen Umgebungen portabel bleiben.
        """
        needle = str(match_text or "").strip()
        if len(needle) < 3:
            raise DjangoValidationError(
                "Für eine Lernregel ist ein Match-Text mit mindestens 3 Zeichen nötig."
            )

        then = {}
        if document.correspondent_id:
            then["correspondent"] = document.correspondent.name
        if document.document_type_id:
            then["document_type"] = document.document_type.name
        if document.storage_path_id:
            then["storage_path"] = document.storage_path.name
        if document.folder_id:
            then["folder"] = document.folder.full_path
        tag_names = list(document.tags.order_by("name").values_list("name", flat=True))
        if tag_names:
            then["tags"] = tag_names

        if not then:
            raise DjangoValidationError(
                "Für eine Lernregel muss mindestens ein Metadatum gesetzt sein."
            )

        match = {"text_contains": [needle]}
        # Owner-Scoping (P1): die gelernte Regel gehört dem Eigentümer des Dokuments
        # und wirkt nur auf dessen Dokumente. Dedup ebenfalls owner-scoped.
        rule_owner_id = document.owner_id
        existing = ClassificationRule.objects.filter(
            match=match, then=then, owner_id=rule_owner_id
        ).first()
        if existing is not None:
            return existing, False

        parts = [
            value
            for value in (
                document.correspondent.name if document.correspondent_id else "",
                document.document_type.name if document.document_type_id else "",
                document.folder.full_path if document.folder_id else "",
            )
            if value
        ]
        label = " · ".join(parts) or document.title
        rule = ClassificationRule.objects.create(
            name=f"Inbox · {label[:220]}",
            priority=90,
            enabled=True,
            match=match,
            then=then,
            owner_id=rule_owner_id,
        )
        return rule, True

    @action(detail=True, methods=["post"])
    def apply_suggestions(self, request, pk=None):
        """Übernimmt KI-Vorschläge ans Dokument (legt Stammdaten bei Bedarf an).

        Body optional: ``{"fields": ["title","correspondent","document_type","date","tags"]}``
        – ohne Angabe werden alle vorhandenen Vorschläge übernommen. Werte werden
        gereinigt (strip + Längen-Cap); Stammdaten werden case-insensitiv
        wiederverwendet (keine Groß/Klein-Duplikate). ``date`` (ISO YYYY-MM-DD)
        wird auf ``Document.created_at`` (Belegdatum) gemappt; ungültige/leere
        Werte werden still ignoriert. Übernommene Felder werden aus
        ``ai_suggestions`` entfernt, der Rest bleibt stehen.
        """
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self.get_object()
        suggestions = dict(document.ai_suggestions or {})
        requested = request.data.get("fields")
        fields = (
            requested
            if isinstance(requested, list)
            else ["title", "correspondent", "document_type", "date", "tags"]
        )

        applied = []
        changed_fields = []

        if "title" in fields and _clean(suggestions.get("title")):
            document.title = _clean(suggestions["title"])
            changed_fields.append("title")
            applied.append("title")
        if "correspondent" in fields and _clean(suggestions.get("correspondent")):
            document.correspondent = _get_or_create_ci(
                Correspondent, _clean(suggestions["correspondent"])
            )
            changed_fields.append("correspondent")
            applied.append("correspondent")
        if "document_type" in fields and _clean(suggestions.get("document_type")):
            document.document_type = _get_or_create_ci(
                DocumentType, _clean(suggestions["document_type"])
            )
            changed_fields.append("document_type")
            applied.append("document_type")
        if "date" in fields:
            parsed = _parse_iso_date(suggestions.get("date"))
            if parsed is not None:
                document.created_at = parsed
                changed_fields.append("created_at")
                applied.append("date")

        if changed_fields:
            document.save(update_fields=changed_fields)

        if "tags" in fields and isinstance(suggestions.get("tags"), list):
            added_any = False
            for name in suggestions["tags"]:
                clean_name = _clean(name, 64)
                if clean_name:
                    tag = _get_or_create_ci(Tag, clean_name, parent=None)
                    document.tags.add(tag)
                    added_any = True
            if added_any:
                applied.append("tags")

        for key in applied:
            suggestions.pop(key, None)
        document.ai_suggestions = suggestions
        document.save(update_fields=["ai_suggestions"])

        if applied:
            AuditLogEntry.objects.create(
                actor=request.user,
                action="apply_suggestions",
                object_type="Document",
                object_id=str(document.id),
                detail={"fields": applied},
            )
        review_task_service.sync_document_review_tasks(document)

        return Response(self.get_serializer(document).data)

    @action(detail=True, methods=["get"], url_path="filing-suggestions")
    def filing_suggestions(self, request, pk=None):
        """Auto-Ablage-Vorschläge (Ordner/Tags/Korrespondent/Typ) per kNN-Embeddings.

        Leitet die Vorschläge aus den inhaltlich ähnlichsten sichtbaren Dokumenten
        ab (owner-gescoped via ``get_queryset``). Rein lokal – kein LLM/API-Key.
        """
        document = self.get_object()
        return Response(
            auto_file_service.suggest_filing(document, self.get_queryset())
        )

    @action(detail=True, methods=["post"], url_path="apply-filing")
    def apply_filing(self, request, pk=None):
        """Übernimmt die Auto-Ablage-Vorschläge: füllt leere Felder, ergänzt Tags.

        Body optional: ``{"fields": ["folder","correspondent","document_type","tags"]}``.
        FK-Felder werden nur gesetzt, wenn sie leer sind (überschreibt keine
        manuelle Wahl); Tags werden ergänzt. Die Vorschläge werden serverseitig neu
        berechnet (kein Vertrauen auf Client-Werte).
        """
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        suggestion = auto_file_service.suggest_filing(document, self.get_queryset())
        if suggestion.get("status") != "ok":
            return Response(suggestion, status=status.HTTP_200_OK)

        requested = request.data.get("fields")
        applied = auto_file_service.apply_filing(
            document,
            suggestion,
            fields=requested if isinstance(requested, list) else None,
        )
        if applied:
            AuditLogEntry.objects.create(
                actor=request.user,
                action="apply_filing",
                object_type="Document",
                object_id=str(document.id),
                detail={"fields": applied},
            )
            review_task_service.sync_document_review_tasks(document)
        document.refresh_from_db()
        return Response(
            {"applied": applied, "document": self.get_serializer(document).data}
        )

    @action(detail=True, methods=["get"], url_path="duplicates")
    def duplicates(self, request, pk=None):
        """Inhaltliche Beinah-Duplikate/Versionen dieses Dokuments (owner-gescoped)."""
        document = self.get_object()
        try:
            threshold = float(request.query_params["threshold"])
        except (KeyError, TypeError, ValueError):
            threshold = None
        return Response(
            duplicates_service.find_duplicates(
                document, self.get_queryset(), threshold=threshold
            )
        )

    @action(detail=True, methods=["post"], url_path="supersede")
    def supersede(self, request, pk=None):
        """Markiert dieses Dokument als Dublette, ersetzt durch ein kanonisches.

        Body: ``{"by": <canonical_id>}``. Soft-Merge: das Dokument bleibt erhalten,
        wird aber aus den Standardlisten ausgeblendet (Undo via ``unsupersede``).
        Keine destruktive Datei-Operation.
        """
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        try:
            canonical_id = int(request.data.get("by"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "Feld 'by' (kanonische Dokument-ID) fehlt oder ist ungültig."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if canonical_id == document.id:
            return Response(
                {"detail": "Ein Dokument kann sich nicht selbst ersetzen."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        canonical = self.get_queryset().filter(pk=canonical_id).first()
        if canonical is None:
            return Response(
                {"detail": "Kanonisches Dokument nicht gefunden."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if canonical.superseded_by_id is not None:
            return Response(
                {"detail": "Das Zieldokument ist selbst als Dublette markiert."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        document.superseded_by = canonical
        document.superseded_at = timezone.now()
        document.save(update_fields=["superseded_by", "superseded_at"])
        AuditLogEntry.objects.create(
            actor=request.user,
            action="supersede",
            object_type="Document",
            object_id=str(document.id),
            detail={"superseded_by": canonical.id},
        )
        return Response(self.get_serializer(document).data)

    @action(detail=True, methods=["post"], url_path="unsupersede")
    def unsupersede(self, request, pk=None):
        """Hebt die Dubletten-Markierung wieder auf (Undo)."""
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        if document.superseded_by_id is not None:
            document.superseded_by = None
            document.superseded_at = None
            document.save(update_fields=["superseded_by", "superseded_at"])
            AuditLogEntry.objects.create(
                actor=request.user,
                action="unsupersede",
                object_type="Document",
                object_id=str(document.id),
                detail={},
            )
        return Response(self.get_serializer(document).data)

    @action(detail=True, methods=["post"], url_path="share-household")
    def share_household(self, request, pk=None):
        """Gibt dieses (eigene) Dokument für den Haushalt frei bzw. hebt die Freigabe auf.

        Body: ``{"shared": true|false}``. Nur der Eigentümer (``can_write``); da
        diese Action NICHT in SAFE_READ_ACTIONS steht, liefert get_object() ohnehin
        nur eigene Dokumente. Freigeben setzt eine Haushalts-Mitgliedschaft voraus.
        """
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        shared = bool(request.data.get("shared", True))
        if shared and not request.user.households.exists():
            return Response(
                {"detail": "Bitte zuerst einen Haushalt anlegen oder beitreten."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        document.shared_with_household = shared
        document.save(update_fields=["shared_with_household"])
        AuditLogEntry.objects.create(
            actor=request.user,
            action="share_household",
            object_type="Document",
            object_id=str(document.id),
            detail={"shared": shared},
        )
        return Response(self.get_serializer(document).data)

    @action(detail=False, methods=["get"], url_path="duplicate-report")
    def duplicate_report(self, request):
        """Korpus-Report: Paare inhaltlicher Beinah-Duplikate im Bestand des Nutzers."""
        try:
            threshold = float(request.query_params["threshold"])
        except (KeyError, TypeError, ValueError):
            threshold = None
        return Response(
            duplicates_service.duplicate_report(self.get_queryset(), threshold=threshold)
        )

    @action(detail=False, methods=["post"], url_path="auto-file-batch")
    def auto_file_batch(self, request):
        """Batch-Autopilot: sortiert alle noch nicht abgelegten Dokumente vor.

        Läuft über die ordnerlosen, verarbeiteten Dokumente des Nutzers und wendet
        pro Dokument nur hoch-sichere Vorschläge an (nur leere Felder). Body optional:
        ``{"min_confidence": 0.8}``. Owner-gescoped via ``get_queryset``; ``can_write``.
        """
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            min_conf = float(request.data.get("min_confidence", settings.AUTO_FILE_MIN_CONFIDENCE))
        except (TypeError, ValueError):
            min_conf = settings.AUTO_FILE_MIN_CONFIDENCE

        visible = list(self.get_queryset())
        targets = [
            doc for doc in visible if doc.folder_id is None and doc.current_version_id
        ][:200]

        results = []
        filed = 0
        for document in targets:
            outcome = auto_file_service.autofile_document(
                document, visible, min_confidence=min_conf
            )
            applied = outcome.get("applied", [])
            if applied:
                filed += 1
                AuditLogEntry.objects.create(
                    actor=request.user,
                    action="auto_file",
                    object_type="Document",
                    object_id=str(document.id),
                    detail={"fields": applied, "min_confidence": min_conf, "batch": True},
                )
                review_task_service.sync_document_review_tasks(document)
            results.append(
                {
                    "document": document.id,
                    "title": document.title,
                    "applied": applied,
                    "status": outcome.get("status"),
                }
            )
        return Response(
            {
                "processed": len(targets),
                "filed": filed,
                "min_confidence": min_conf,
                "results": results,
            }
        )

    @action(detail=True, methods=["post"], url_path="suggest")
    def suggest(self, request, pk=None):
        """Regeneriert die KI-Metadatenvorschläge synchron (sofortiges UI-Feedback).

        Ruft die bestehende Generierung (``suggest_metadata``) direkt im Request
        auf und schreibt das Ergebnis nach ``ai_suggestions`` (+ ``ai_suggested_at``).
        Ist kein Provider verfügbar, wird nichts geschrieben und ``source`` ist
        ``"unavailable"`` (Status 200, damit die UI sauber reagieren kann).
        Owner-Scoping über ``get_object()``; Schreiben nur für ``can_write``.
        """
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self.get_object()
        # Lazy-Import: vermeidet Zyklus documents <-> ai zur Ladezeit.
        from ai.services import suggest_metadata

        version = document.current_version
        text = (version.ocr_text if version else "") or ""
        result = suggest_metadata(text)
        suggestions = result.get("suggestions") or {}

        if result.get("source") == "ai" and suggestions:
            # Bereits hinterlegte Vorschläge erhalten; KI hat bei Überschneidung Vorrang.
            merged = {**(document.ai_suggestions or {}), **suggestions}
            document.ai_suggestions = merged
            document.ai_suggested_at = timezone.now()
            document.save(update_fields=["ai_suggestions", "ai_suggested_at"])
            AuditLogEntry.objects.create(
                actor=request.user,
                action="suggest",
                object_type="Document",
                object_id=str(document.id),
                detail={
                    "provider": result.get("provider"),
                    "keys": sorted(suggestions.keys()),
                },
            )
            review_task_service.sync_document_review_tasks(document)

        data = self.get_serializer(document).data
        return Response({**data, "source": result.get("source", "unavailable")})

    @action(detail=True, methods=["post"], url_path="dismiss_suggestions")
    def dismiss_suggestions(self, request, pk=None):
        """Verwirft einzelne KI-Vorschläge, OHNE sie anzuwenden (FE-Reject).

        Body: ``{"fields": ["title","date",...]}`` – die genannten Schlüssel
        werden aus ``ai_suggestions`` entfernt und der Rest gespeichert. Spiegelt
        die Struktur von ``apply_suggestions``. Owner-Scoping über ``get_object()``;
        Schreiben nur für ``can_write``.
        """
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self.get_object()
        requested = request.data.get("fields")
        fields = requested if isinstance(requested, list) else []

        suggestions = dict(document.ai_suggestions or {})
        removed = [f for f in fields if isinstance(f, str) and f in suggestions]
        for key in removed:
            suggestions.pop(key, None)

        if removed:
            document.ai_suggestions = suggestions
            document.save(update_fields=["ai_suggestions"])
            AuditLogEntry.objects.create(
                actor=request.user,
                action="dismiss_suggestions",
                object_type="Document",
                object_id=str(document.id),
                detail={"fields": removed},
            )
        review_task_service.sync_document_review_tasks(document)

        return Response(self.get_serializer(document).data)

    # --- Freigabe-Workflow (STOAA-63) ------------------------------------
    def _transition(self, request, allowed_from, new_status, action_name):
        """Gemeinsame Logik der Freigabe-Übergänge submit/approve/reject.

        Prüft Schreibrecht (403 für Gäste), lädt owner-gescoped über
        ``get_object()`` und lässt nur gültige Statusübergänge zu – ein
        unzulässiger Übergang liefert **409 Conflict** (sauberer 4xx, nie 500).
        Nach dem Übergang wird ``status`` gespeichert und genau ein
        ``AuditLogEntry`` mit ``from``/``to`` geschrieben. Antwort: 200 mit
        dem serialisierten Dokument (das FE erhält den neuen Status direkt).
        """
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self.get_object()
        old_status = document.status
        if old_status not in allowed_from:
            return Response(
                {
                    "detail": (
                        f"Übergang aus Status '{old_status}' nach '{new_status}' "
                        "nicht erlaubt."
                    ),
                    "status": old_status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        reason = request.data.get("reason") if action_name == "reject" else None
        document.status = new_status
        document.save(update_fields=["status"])
        AuditLogEntry.objects.create(
            actor=request.user,
            action=action_name,
            object_type="Document",
            object_id=str(document.id),
            detail={"from": old_status, "to": new_status, "reason": reason or None},
        )
        return Response(self.get_serializer(document).data)

    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request, pk=None):
        """Dokument zur Freigabe einreichen: entwurf|abgelehnt → zur_freigabe."""
        return self._transition(
            request,
            allowed_from=(
                Document.ApprovalStatus.ENTWURF,
                Document.ApprovalStatus.ABGELEHNT,
            ),
            new_status=Document.ApprovalStatus.ZUR_FREIGABE,
            action_name="submit",
        )

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        """Freigeben: zur_freigabe → freigegeben."""
        return self._transition(
            request,
            allowed_from=(Document.ApprovalStatus.ZUR_FREIGABE,),
            new_status=Document.ApprovalStatus.FREIGEGEBEN,
            action_name="approve",
        )

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        """Ablehnen: zur_freigabe → abgelehnt. Grund optional aus ``reason``."""
        return self._transition(
            request,
            allowed_from=(Document.ApprovalStatus.ZUR_FREIGABE,),
            new_status=Document.ApprovalStatus.ABGELEHNT,
            action_name="reject",
        )

    @action(detail=True, methods=["post"], url_path="retry_processing")
    def retry_processing(self, request, pk=None):
        """Verarbeitung der aktuellen Version neu anstoßen (STOAA-248).

        Bewusst dokument-scoped (nicht version-scoped): es gibt kein
        DocumentVersionViewSet, Versionen sind nur nested; der UI-Bedarf ist
        genau der Retry der *current_version*. Die Owner-Isolation kommt gratis
        über ``get_object()`` (fremdes Dokument → 404).

        Guard: nur erlaubt, wenn ``current_version.processing_state == failed``
        (sonst 400). Gast-Rolle → 403. Die – potentiell lange –
        Neuverarbeitung läuft asynchron (``retry_document_version.delay``); die
        Antwort ist 202 mit der serialisierten aktuellen Version (noch im
        Zustand FAILED, das Hochzählen auf RETRY_PENDING passiert im Task).
        Polling ist nicht nötig – der ``processing_state``-Rollup aktualisiert
        sich über die Liste.
        """
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self.get_object()
        version = document.current_version
        PS = DocumentVersion.ProcessingState

        if version is None or version.processing_state != PS.FAILED:
            return Response(
                {
                    "detail": (
                        "Retry ist nur für eine fehlgeschlagene Verarbeitung "
                        "(processing_state=failed) möglich."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        retry_document_version.delay(version.id, actor_id=request.user.id)
        return Response(
            DocumentVersionSerializer(version).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="mark_reviewed")
    def mark_reviewed(self, request, pk=None):
        """Dokument aus der Review-Inbox nehmen.

        Das ist absichtlich eine eigene Action statt freiem PATCH auf
        ``review_status``: die fachliche Bestätigung ist ein Nutzerereignis und
        bleibt damit später leicht auditierbar/erweiterbar.

        Optionaler Lernmodus::

            {"create_rule": true, "match_text": "Wüstenrot Gruppe"}

        Dann wird aus den bestätigten Metadaten eine deterministische
        Klassifizierungsregel erzeugt. Ohne expliziten Match-Text gibt es keine
        Regel - bewusst kein stilles Lernen.
        """
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self.get_object()
        learned_rule = None
        learned_created = False
        learn_payload = request.data.get("learn")
        learn_payload = learn_payload if isinstance(learn_payload, dict) else {}
        create_rule = bool(request.data.get("create_rule") or learn_payload)
        match_text = (
            request.data.get("match_text")
            or learn_payload.get("text_contains")
            or learn_payload.get("match_text")
            or ""
        )

        with transaction.atomic():
            if create_rule:
                try:
                    learned_rule, learned_created = self._create_review_rule(
                        document, match_text
                    )
                except DjangoValidationError as exc:
                    return Response(
                        {"detail": "; ".join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                AuditLogEntry.objects.create(
                    actor=request.user,
                    action="create_classification_rule_from_review",
                    object_type="Document",
                    object_id=str(document.id),
                    detail={
                        "rule": learned_rule.id,
                        "created": learned_created,
                        "match_text": match_text,
                    },
                )

            if document.review_status != Document.ReviewStatus.REVIEWED:
                document.review_status = Document.ReviewStatus.REVIEWED
                document.save(update_fields=["review_status"])
                resolved_tasks = review_task_service.resolve_review_tasks(
                    document,
                    actor=request.user,
                    reason="document_marked_reviewed",
                )
                AuditLogEntry.objects.create(
                    actor=request.user,
                    action="mark_reviewed",
                    object_type="Document",
                    object_id=str(document.id),
                    detail={
                        "review_status": Document.ReviewStatus.REVIEWED,
                        "resolved_review_tasks": resolved_tasks,
                    },
                )

        serializer = self.get_serializer(document)
        data = dict(serializer.data)
        if learned_rule is not None:
            data["learned_rule"] = ClassificationRuleSerializer(learned_rule).data
            data["learned_rule_created"] = learned_created
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="inbox-summary")
    def inbox_summary(self, request):
        """Kompakter Mailroom-Rollup für Queue-Kacheln im Frontend."""
        inbox = self.get_queryset().filter(
            review_status=Document.ReviewStatus.NEEDS_REVIEW
        )
        PS = DocumentVersion.ProcessingState
        processing_states = [
            PS.UPLOADED,
            PS.HASHED,
            PS.OCR_RUNNING,
            PS.OCR_DONE,
            PS.CLASSIFICATION_RUNNING,
            PS.CLASSIFIED,
            PS.THUMBNAIL_DONE,
            PS.SEALED,
        ]
        oldest = inbox.order_by("added_at").values_list("added_at", flat=True).first()
        open_tasks = DocumentReviewTask.objects.filter(
            document__in=inbox,
            status=DocumentReviewTask.Status.OPEN,
        )
        task_kinds = {
            item["kind"]: item["count"]
            for item in open_tasks.values("kind").annotate(count=Count("id"))
        }
        return Response(
            {
                "total_needs_review": inbox.count(),
                "ready": inbox.filter(current_version__processing_state=PS.READY).count(),
                "processing": inbox.filter(
                    current_version__processing_state__in=processing_states
                ).count(),
                "failed": inbox.filter(
                    current_version__processing_state=PS.FAILED
                ).count(),
                "retry_pending": inbox.filter(
                    current_version__processing_state=PS.RETRY_PENDING
                ).count(),
                "with_ai_suggestions": inbox.exclude(ai_suggestions={}).count(),
                "pending_extraction_candidates": ExtractionCandidate.objects.filter(
                    document__in=inbox,
                    status=ExtractionCandidate.Status.PENDING,
                ).count(),
                "pending_case_candidates": CaseFileCandidate.objects.filter(
                    document__in=inbox,
                    status=CaseFileCandidate.Status.PENDING,
                ).count(),
                "open_review_tasks": open_tasks.count(),
                "review_task_kinds": task_kinds,
                "oldest_added_at": oldest,
            }
        )

    @action(detail=False, methods=["get"], url_path="autopilot-inbox")
    def autopilot_inbox(self, request):
        """Verdichtet die Review-Inbox zu einer Ablage-Autopilot-Entscheidung."""
        try:
            limit = int(request.query_params.get("limit", 25))
        except (TypeError, ValueError):
            limit = 25
        limit = max(1, min(limit, 100))

        inbox = (
            self.get_queryset()
            .filter(review_status=Document.ReviewStatus.NEEDS_REVIEW)
            .select_related(
                "correspondent",
                "document_type",
                "storage_path",
                "folder",
                "current_version",
            )
            .prefetch_related(
                "tags",
                "review_tasks",
                "extraction_candidates",
                "case_file_candidates__case_file",
            )
        )
        total = inbox.count()
        documents = list(inbox.order_by("-added_at", "-id")[:limit])

        from .services import autopilot

        payload = autopilot.build_inbox(documents, total=total)
        payload["limit"] = limit
        return Response(payload)

    @action(detail=False, methods=["post"], url_path="inbox-generate-candidates")
    def inbox_generate_candidates(self, request):
        """Erzeugt Smart-Inbox- und Aktenvorschläge für mehrere Dokumente."""
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            requested_ids = self._parse_document_ids(request.data.get("ids"))
        except DjangoValidationError as exc:
            return Response(
                {"detail": "; ".join(exc.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        documents, errors = self._resolve_scoped_documents(requested_ids)
        from .services import case_matching, extraction

        extraction_created = 0
        case_created = 0
        item_errors = list(errors)
        for document in documents:
            try:
                extraction_created += extraction.generate_candidates(document)
                case_created += case_matching.generate_candidates(document)
                review_task_service.sync_document_review_tasks(document)
            except Exception as exc:  # noqa: BLE001
                item_errors.append({"id": document.id, "error": str(exc)})

        if documents:
            AuditLogEntry.objects.create(
                actor=request.user,
                action="inbox_generate_candidates",
                object_type="Document",
                object_id=f"{len(documents)} Dokumente",
                detail={
                    "ids": sorted(document.id for document in documents),
                    "extraction_created": extraction_created,
                    "case_created": case_created,
                    "errors": item_errors,
                },
            )

        return Response(
            {
                "documents": len(documents),
                "extraction_created": extraction_created,
                "case_created": case_created,
                "errors": item_errors,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="mark-reviewed-bulk")
    def mark_reviewed_bulk(self, request):
        """Schließt mehrere Mailroom-Einträge in einem Schritt ab."""
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            requested_ids = self._parse_document_ids(request.data.get("ids"))
        except DjangoValidationError as exc:
            return Response(
                {"detail": "; ".join(exc.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        documents, errors = self._resolve_scoped_documents(requested_ids)
        updated_ids = []
        unchanged = 0
        with transaction.atomic():
            for document in documents:
                if document.review_status == Document.ReviewStatus.REVIEWED:
                    unchanged += 1
                    continue
                document.review_status = Document.ReviewStatus.REVIEWED
                document.save(update_fields=["review_status"])
                updated_ids.append(document.id)
                resolved_tasks = review_task_service.resolve_review_tasks(
                    document,
                    actor=request.user,
                    reason="bulk_mark_reviewed",
                )
                AuditLogEntry.objects.create(
                    actor=request.user,
                    action="mark_reviewed",
                    object_type="Document",
                    object_id=str(document.id),
                    detail={
                        "review_status": Document.ReviewStatus.REVIEWED,
                        "mode": "bulk",
                        "resolved_review_tasks": resolved_tasks,
                    },
                )

            if updated_ids:
                AuditLogEntry.objects.create(
                    actor=request.user,
                    action="mark_reviewed_bulk",
                    object_type="Document",
                    object_id=f"{len(updated_ids)} Dokumente",
                    detail={"ids": sorted(updated_ids), "errors": errors},
                )

        return Response(
            {"updated": len(updated_ids), "unchanged": unchanged, "errors": errors},
            status=status.HTTP_200_OK,
        )

    def _resolve_version_no(self, document, version_no):
        """Löst eine ``version_no`` gegen die DB auf (kein Nutzerpfad).

        Wie das bestehende ``_resolve_version``-Muster: die Nummer wird gegen
        ``document.versions`` validiert, es gibt keine Nutzer-Dateipfade → keine
        Traversal. Fehlende/ungültige Version → ``Http404``.
        """
        version = document.versions.filter(version_no=version_no).first()
        if version is None:
            raise Http404(f"Version {version_no} nicht vorhanden.")
        return version

    @action(
        detail=True,
        methods=["get"],
        url_path=(
            "versions/(?P<from_version>[0-9]+)/compare/(?P<to_version>[0-9]+)"
        ),
    )
    def compare_versions(
        self, request, pk=None, from_version=None, to_version=None
    ):
        """Vergleicht zwei Versionen (Stufe 1: OCR-/Datei-/PDF-Diff, STOAA-289).

        Pfad::

            GET /api/documents/{id}/versions/{from_version}/compare/{to_version}/

        ``from_version``/``to_version`` sind ``version_no``-Werte (``from`` = alt,
        ``to`` = neu); beliebige Reihenfolge ist erlaubt. ``self.get_object()``
        erzwingt Sichtbarkeit/Owner über das gefilterte Queryset – fremde/nicht
        sichtbare Dokumente ergeben 404, keine neuen Rechte. Fehlende/ungültige
        Version → 404. Die gesamte Vergleichslogik liegt im Service
        ``services.version_compare`` – hier nur Auflösung + Delegation.
        """
        document = self.get_object()
        # ``from_version``/``to_version`` sind durch die url_path-Regex bereits
        # rein numerisch; int() ist damit crash-frei.
        old = self._resolve_version_no(document, int(from_version))
        new = self._resolve_version_no(document, int(to_version))
        result = version_compare.compare_versions(document, old, new)
        return Response(result.to_dict())

    @action(detail=True, methods=["get"], url_path="pdf-workbench/pages")
    def pdf_workbench_pages(self, request, pk=None):
        """Seitenmanifest der aktuellen PDF-Version für die Werkbank."""
        document = self.get_object()
        if document.current_version is None:
            return Response(
                {"detail": "Dokument hat keine aktuelle Version."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from .services import pdf_workbench

        try:
            manifest = pdf_workbench.page_manifest(document.current_version)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"PDF konnte nicht gelesen werden: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"document": document.id, **manifest})

    @action(
        detail=True,
        methods=["get"],
        url_path=r"pdf-workbench/pages/(?P<page_no>[0-9]+)/thumbnail",
    )
    def pdf_workbench_page_thumbnail(self, request, pk=None, page_no=None):
        """JPEG-Miniatur einer einzelnen PDF-Seite für die visuelle Werkbank."""
        document = self.get_object()
        if document.current_version is None:
            return Response(
                {"detail": "Dokument hat keine aktuelle Version."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from .services import pdf_workbench

        try:
            data = pdf_workbench.render_page_thumbnail(
                document.current_version,
                int(page_no),
            )
        except DjangoValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Miniatur konnte nicht erzeugt werden: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return HttpResponse(data, content_type="image/jpeg")

    @action(detail=True, methods=["post"], url_path="pdf-workbench/rewrite")
    def pdf_workbench_rewrite(self, request, pk=None):
        """Reorder/Delete/Rotate als neue Version desselben Dokuments."""
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        from .services import pdf_workbench

        try:
            specs = pdf_workbench.parse_page_specs(request.data.get("pages"))
            version = pdf_workbench.rewrite_as_new_version(
                document,
                specs,
                actor=request.user,
                reason=str(request.data.get("reason", "")),
            )
        except DjangoValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return Response({"detail": str(exc)}, status=400)

        _enqueue_processing(version.id)
        document.refresh_from_db()
        return Response(self.get_serializer(document).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="pdf-workbench/merge")
    def pdf_workbench_merge(self, request, pk=None):
        """Merged weitere sichtbare Dokumente in eine neue Version dieses Dokuments."""
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        raw_ids = request.data.get("document_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return Response(
                {"detail": "Feld 'document_ids' muss eine nicht-leere Liste sein."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            ids = [int(raw) for raw in raw_ids if int(raw) != document.id]
        except (TypeError, ValueError):
            return Response(
                {"detail": "Alle document_ids müssen Zahlen sein."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ids = list(dict.fromkeys(ids))
        qs = Document.objects.filter(id__in=ids).select_related("current_version")
        if not getattr(request.user, "is_dms_admin", False):
            qs = qs.filter(owner=request.user)
        visible = list(qs)
        if len(visible) != len(ids):
            return Response(
                {"detail": "Mindestens ein Merge-Dokument ist nicht sichtbar."},
                status=status.HTTP_404_NOT_FOUND,
            )

        ordered = sorted(visible, key=lambda item: ids.index(item.id))
        from .services import pdf_workbench

        try:
            version = pdf_workbench.merge_as_new_version(
                document,
                ordered,
                actor=request.user,
                reason=str(request.data.get("reason", "")),
            )
        except DjangoValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return Response({"detail": str(exc)}, status=400)

        _enqueue_processing(version.id)
        document.refresh_from_db()
        return Response(self.get_serializer(document).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="pdf-workbench/split")
    def pdf_workbench_split(self, request, pk=None):
        """Splittet Seitenbereiche in neue Dokumente."""
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self.get_object()
        parts = request.data.get("parts")
        if not isinstance(parts, list) or not parts:
            return Response(
                {"detail": "Feld 'parts' muss eine nicht-leere Liste sein."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        for idx, part in enumerate(parts, start=1):
            if not isinstance(part, dict) or not isinstance(part.get("pages"), list):
                return Response(
                    {"detail": f"Teil {idx} braucht eine pages-Liste."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        from .services import pdf_workbench

        try:
            created = pdf_workbench.split_into_documents(document, parts, actor=request.user)
        except DjangoValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=400)
        except Exception as exc:  # noqa: BLE001
            return Response({"detail": str(exc)}, status=400)

        for _created_document, version in created:
            _enqueue_processing(version.id)
        serializer = self.get_serializer([item[0] for item in created], many=True)
        return Response({"documents": serializer.data}, status=status.HTTP_201_CREATED)

    # Bis zu so vielen Dokumenten wird synchron im Request klassifiziert;
    # größere Batches wandern in einen Celery-Task (Timeout-/Lastschutz).
    BULK_CLASSIFY_SYNC_LIMIT = 10

    @action(detail=False, methods=["post"], url_path="bulk-classify")
    def bulk_classify(self, request):
        """Klassifizierungsregeln auf mehrere Dokumente anwenden (Massenaktion).

        Body::

            {"ids": [<int>, ...]}

        Für jedes eigene Dokument wird ``classification.apply_rules`` erneut
        angewandt. Kleine Batches (≤ ``BULK_CLASSIFY_SYNC_LIMIT``) werden synchron
        verarbeitet und liefern direkt die Zählung zurück::

            {"updated": 8, "unchanged": 2, "errors": [...]}

        Größere Batches (> Limit) werden an den Celery-Task
        ``bulk_classify_documents`` übergeben; die Antwort enthält die Task-ID::

            {"task_id": "abc-123", "status": "processing"}

        Owner-Isolation (STOAA-7): Es wird ausschließlich über ``get_queryset()``
        gescopet – fremde/unbekannte IDs wirken nicht und werden (synchron) als
        ``errors``-Eintrag gemeldet (kein 404-Enumeration, kein Leak).
        Schreibrecht (``can_write``) erforderlich.
        """
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        raw_ids = request.data.get("ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return Response(
                {"detail": "Feld 'ids' muss eine nicht-leere Liste sein."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # IDs normalisieren (nur ganzzahlige, Duplikate zusammenfassen).
        requested_ids = []
        for rid in raw_ids:
            try:
                requested_ids.append(int(rid))
            except (TypeError, ValueError):
                return Response(
                    {"detail": f"Ungültige Dokument-ID: {rid!r}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        requested_ids = list(dict.fromkeys(requested_ids))

        # Owner-Scoping: nur eigene (bzw. als Admin: alle) Dokumente.
        owned_ids = list(
            self.get_queryset().filter(id__in=requested_ids).values_list("id", flat=True)
        )
        # Nicht auffindbare/fremde IDs → als Teilfehler melden (kein Leak).
        skipped = [rid for rid in requested_ids if rid not in set(owned_ids)]
        errors = [
            {"id": rid, "error": "nicht gefunden oder keine Berechtigung"}
            for rid in skipped
        ]

        # Große Batches asynchron verarbeiten (Timeout-/Lastschutz).
        if len(owned_ids) > self.BULK_CLASSIFY_SYNC_LIMIT:
            task = bulk_classify_documents.delay(owned_ids, actor_id=request.user.id)
            return Response({"task_id": task.id, "status": "processing"})

        # Kleine Batches synchron; frische Instanzen mit Prefetch für apply_rules.
        documents = list(self.get_queryset().filter(id__in=owned_ids))
        result = classification.classify_documents(documents)
        result["errors"] = result["errors"] + errors

        if documents:
            AuditLogEntry.objects.create(
                actor=request.user,
                action="bulk_classify",
                object_type="Document",
                object_id=f"{len(documents)} Dokumente",
                detail={
                    "mode": "sync",
                    "ids": sorted(owned_ids),
                    "updated": result["updated"],
                    "unchanged": result["unchanged"],
                    "errors": result["errors"],
                },
            )

        return Response(result)

    @action(detail=False, methods=["post"], url_path="bulk-update")
    def bulk_update(self, request):
        """Metadaten mehrerer Dokumente in einem Schritt ändern.

        Body::

            {
              "ids": [1, 2, 3],
              "set": {
                "folder": 5,
                "document_type": 2,
                "correspondent": null,
                "review_status": "reviewed"
              },
              "add_tags": [1, 2],
              "remove_tags": [3]
            }

        Nur eigene bzw. für Admins sichtbare Dokumente werden geändert. Fremde
        oder unbekannte IDs erscheinen als Teilfehler, damit die UI große
        Auswahlen robust verarbeiten kann, ohne Objekt-Existenz zu leaken.
        """
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )

        raw_ids = request.data.get("ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return Response(
                {"detail": "Feld 'ids' muss eine nicht-leere Liste sein."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        requested_ids = []
        for rid in raw_ids:
            try:
                requested_ids.append(int(rid))
            except (TypeError, ValueError):
                return Response(
                    {"detail": f"Ungültige Dokument-ID: {rid!r}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        requested_ids = list(dict.fromkeys(requested_ids))

        set_values = request.data.get("set") or {}
        if not isinstance(set_values, dict):
            return Response(
                {"detail": "Feld 'set' muss ein Objekt sein."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        def _int_or_none(value, field_name):
            if value in ("", None):
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ValueError(f"Ungültiger Wert für {field_name}: {value!r}.")

        unset = object()

        try:
            folder_id = (
                _int_or_none(set_values["folder"], "folder")
                if "folder" in set_values
                else unset
            )
            document_type_id = (
                _int_or_none(set_values["document_type"], "document_type")
                if "document_type" in set_values
                else unset
            )
            correspondent_id = (
                _int_or_none(set_values["correspondent"], "correspondent")
                if "correspondent" in set_values
                else unset
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        review_status = set_values.get("review_status", unset)
        if review_status is not unset and review_status not in {
            choice for choice, _label in Document.ReviewStatus.choices
        }:
            return Response(
                {"detail": f"Ungültiger review_status: {review_status!r}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        def _ids_list(name):
            raw = request.data.get(name, [])
            if raw in (None, ""):
                return []
            if not isinstance(raw, list):
                raise ValueError(f"Feld '{name}' muss eine Liste sein.")
            try:
                return list(dict.fromkeys(int(value) for value in raw))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Ungültige Tag-ID in '{name}'.") from exc

        try:
            add_tag_ids = _ids_list("add_tags")
            remove_tag_ids = _ids_list("remove_tags")
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if (
            folder_id is unset
            and document_type_id is unset
            and correspondent_id is unset
            and review_status is unset
            and not add_tag_ids
            and not remove_tag_ids
        ):
            return Response(
                {"detail": "Keine Massenänderung angegeben."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        errors = []
        if (
            folder_id is not unset
            and folder_id is not None
            and not DocumentFolder.objects.filter(id=folder_id).exists()
        ):
            errors.append({"field": "folder", "error": "Ordner nicht gefunden."})
        if (
            document_type_id is not unset
            and document_type_id is not None
            and not DocumentType.objects.filter(id=document_type_id).exists()
        ):
            errors.append(
                {"field": "document_type", "error": "Dokumenttyp nicht gefunden."}
            )
        if (
            correspondent_id is not unset
            and correspondent_id is not None
            and not Correspondent.objects.filter(id=correspondent_id).exists()
        ):
            errors.append(
                {"field": "correspondent", "error": "Korrespondent nicht gefunden."}
            )

        known_tag_ids = set(
            Tag.objects.filter(id__in=add_tag_ids + remove_tag_ids).values_list(
                "id", flat=True
            )
        )
        for tag_id in sorted((set(add_tag_ids) | set(remove_tag_ids)) - known_tag_ids):
            errors.append(
                {"field": "tags", "id": tag_id, "error": "Tag nicht gefunden."}
            )
        if errors:
            return Response(
                {"detail": "Ungültige Massenänderung.", "errors": errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        owned_ids = list(
            self.get_queryset().filter(id__in=requested_ids).values_list("id", flat=True)
        )
        skipped = [rid for rid in requested_ids if rid not in set(owned_ids)]
        item_errors = [
            {"id": rid, "error": "nicht gefunden oder keine Berechtigung"}
            for rid in skipped
        ]

        update_fields = []
        if folder_id is not unset:
            update_fields.append("folder")
        if document_type_id is not unset:
            update_fields.append("document_type")
        if correspondent_id is not unset:
            update_fields.append("correspondent")
        if review_status is not unset:
            update_fields.append("review_status")

        updated = 0
        with transaction.atomic():
            documents = list(self.get_queryset().filter(id__in=owned_ids))
            for document in documents:
                if folder_id is not unset:
                    document.folder_id = folder_id
                if document_type_id is not unset:
                    document.document_type_id = document_type_id
                if correspondent_id is not unset:
                    document.correspondent_id = correspondent_id
                if review_status is not unset:
                    document.review_status = review_status
                if update_fields:
                    document.save(update_fields=update_fields)
                if add_tag_ids:
                    document.tags.add(*add_tag_ids)
                if remove_tag_ids:
                    document.tags.remove(*remove_tag_ids)
                updated += 1

            if documents:
                AuditLogEntry.objects.create(
                    actor=request.user,
                    action="bulk_update",
                    object_type="Document",
                    object_id=f"{len(documents)} Dokumente",
                    detail={
                        "ids": sorted(owned_ids),
                        "set": {
                            key: value
                            for key, value in {
                                "folder": None if folder_id is unset else folder_id,
                                "document_type": None
                                if document_type_id is unset
                                else document_type_id,
                                "correspondent": None
                                if correspondent_id is unset
                                else correspondent_id,
                                "review_status": None
                                if review_status is unset
                                else review_status,
                            }.items()
                            if value is not None or key in set_values
                        },
                        "add_tags": add_tag_ids,
                        "remove_tags": remove_tag_ids,
                        "errors": item_errors,
                    },
                )

        return Response(
            {
                "updated": updated,
                "errors": item_errors,
            },
            status=status.HTTP_200_OK,
        )


class DocumentReviewTaskViewSet(viewsets.ReadOnlyModelViewSet):
    """Offene Klärungsaufgaben der Review-Inbox.

    Der ViewSet ist bewusst task-zentriert: Die Dokumentliste zeigt Tasks nested
    an, aber für gezielte Aktionen (einzelnen Hinweis erledigen/ignorieren)
    braucht das Frontend stabile Endpunkte. Owner-Isolation läuft über das
    verknüpfte Dokument; fremde Tasks ergeben 404.
    """

    serializer_class = DocumentReviewTaskSerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        qs = DocumentReviewTask.objects.select_related(
            "document",
            "document__current_version",
            "resolved_by",
        ).order_by("status", "priority", "created_at")
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(document__owner=user)

        params = self.request.query_params
        status_value = params.get("status")
        if status_value in {choice for choice, _label in DocumentReviewTask.Status.choices}:
            qs = qs.filter(status=status_value)
        kind = params.get("kind")
        if kind in {choice for choice, _label in DocumentReviewTask.Kind.choices}:
            qs = qs.filter(kind=kind)
        document_id = params.get("document")
        if document_id:
            qs = qs.filter(document_id=document_id)
        return qs

    def _finish(self, request, *, target_status, reason):
        if not request.user.can_write:
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        task = self.get_object()
        review_task_service.resolve_review_tasks(
            task.document,
            actor=request.user,
            task_ids=[task.id],
            target_status=target_status,
            reason=reason,
        )
        task.refresh_from_db()
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        return self._finish(
            request,
            target_status=DocumentReviewTask.Status.RESOLVED,
            reason=str(request.data.get("reason", "manual_resolve")),
        )

    @action(detail=True, methods=["post"])
    def ignore(self, request, pk=None):
        return self._finish(
            request,
            target_status=DocumentReviewTask.Status.IGNORED,
            reason=str(request.data.get("reason", "manual_ignore")),
        )


class DossierViewSet(viewsets.ModelViewSet):
    """Dossier Builder: gespeicherte, quellengebundene Rechercheakten."""

    serializer_class = DossierSerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        qs = (
            Dossier.objects.select_related("owner")
            .prefetch_related(
                "documents",
                "documents__correspondent",
                "documents__document_type",
                "documents__folder",
                "documents__current_version",
            )
            .annotate(document_count=Count("documents", distinct=True))
            .order_by("-updated_at", "-created_at")
        )
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(owner=user)

        status_value = self.request.query_params.get("status")
        if status_value in {choice for choice, _label in Dossier.Status.choices}:
            qs = qs.filter(status=status_value)

        q = self.request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(query__icontains=q)
                | Q(summary__icontains=q)
                | Q(documents__title__icontains=q)
            )
        return qs.distinct()

    def perform_create(self, serializer):
        dossier = serializer.save(owner=self.request.user)
        AuditLogEntry.objects.create(
            actor=self.request.user,
            action="dossier_create",
            object_type="Dossier",
            object_id=str(dossier.id),
            detail={"title": dossier.title, "query": dossier.query[:500]},
        )

    def _visible_documents(self):
        qs = (
            Document.objects.select_related(
                "correspondent",
                "document_type",
                "folder",
                "case_file",
                "current_version",
                "contract_record",
            )
            .prefetch_related("tags", "current_version__page_texts")
            .exclude(current_version__isnull=True)
            .order_by("-added_at")
        )
        if not getattr(self.request.user, "is_dms_admin", False):
            qs = qs.filter(owner=self.request.user)
        return qs

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        """Generiert das Dossier aus sichtbaren Dokumentquellen."""
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        dossier = self.get_object()
        if dossier.status == Dossier.Status.FINAL:
            return Response(
                {"detail": "Finale Dossiers können nicht neu generiert werden."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        generated = dossier_service.generate_dossier(
            dossier,
            self._visible_documents().distinct()[:400],
        )
        AuditLogEntry.objects.create(
            actor=request.user,
            action="dossier_generate",
            object_type="Dossier",
            object_id=str(generated.id),
            detail={
                "source": generated.generated_source,
                "sources": [source.get("document") for source in generated.sources],
            },
        )
        return Response(self.get_serializer(generated).data)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        """Markiert ein Dossier als final."""
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        dossier = self.get_object()
        dossier.status = Dossier.Status.FINAL
        dossier.save(update_fields=["status", "updated_at"])
        AuditLogEntry.objects.create(
            actor=request.user,
            action="dossier_finalize",
            object_type="Dossier",
            object_id=str(dossier.id),
            detail={},
        )
        return Response(self.get_serializer(dossier).data)

    @action(detail=True, methods=["get"], url_path="export-markdown")
    def export_markdown(self, request, pk=None):
        """Exportiert das Dossier als Markdown-Datei."""
        dossier = self.get_object()
        content = dossier_service.render_markdown(dossier)
        filename = f"{slugify(dossier.title) or 'dossier'}-{dossier.id}.md"
        response = HttpResponse(content, content_type="text/markdown; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ContractRecordViewSet(viewsets.ModelViewSet):
    """Contract Center: Verträge, Fristen und Prüfstatus je Dokument.

    Die Erkennung läuft deterministisch aus OCR/Metadaten. Nutzer können
    erkannte Datensätze bestätigen oder manuell korrigieren; Wiedervorlagen und
    Review-Aufgaben werden synchron gehalten. Owner-Isolation folgt dem
    verknüpften Dokument: fremde Verträge/Dokumente sind 404 statt Datenleck.
    """

    queryset = ContractRecord.objects.all()
    serializer_class = ContractRecordSerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        qs = ContractRecord.objects.select_related(
            "document",
            "document__correspondent",
            "document__current_version",
            "case_file",
            "extracted_from_version",
            "reviewed_by",
        ).order_by(
            "-needs_review",
            "cancel_until",
            "next_due_on",
            "provider",
        )
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(document__owner=user)

        params = self.request.query_params
        status_value = params.get("status")
        if status_value in {choice for choice, _label in ContractRecord.Status.choices}:
            qs = qs.filter(status=status_value)
        contract_type = params.get("contract_type")
        if contract_type in {
            choice for choice, _label in ContractRecord.ContractType.choices
        }:
            qs = qs.filter(contract_type=contract_type)
        needs_review = params.get("needs_review")
        if needs_review in {"1", "true", "yes"}:
            qs = qs.filter(needs_review=True)
        elif needs_review in {"0", "false", "no"}:
            qs = qs.filter(needs_review=False)
        return qs

    def _ensure_document_visible(self, document):
        user = self.request.user
        if getattr(user, "is_dms_admin", False):
            return
        if document is None or document.owner_id != user.id:
            raise Http404("Dokument nicht gefunden.")

    def perform_create(self, serializer):
        document = serializer.validated_data.get("document")
        self._ensure_document_visible(document)
        record = serializer.save(source=ContractRecord.Source.MANUAL)
        contract_service.ensure_contract_reminders(record)
        if record.needs_review:
            contract_service.sync_contract_review_task(record, actor=self.request.user)
        else:
            contract_service.confirm_contract(record, actor=self.request.user)
        AuditLogEntry.objects.create(
            actor=self.request.user,
            action="contract_manual_create",
            object_type="ContractRecord",
            object_id=str(record.id),
            detail={"document": record.document_id},
        )

    def perform_update(self, serializer):
        document = serializer.validated_data.get(
            "document", getattr(serializer.instance, "document", None)
        )
        self._ensure_document_visible(document)
        record = serializer.save(source=ContractRecord.Source.MANUAL)
        contract_service.ensure_contract_reminders(record)
        if record.needs_review:
            contract_service.sync_contract_review_task(record, actor=self.request.user)
        else:
            contract_service.confirm_contract(record, actor=self.request.user)
        AuditLogEntry.objects.create(
            actor=self.request.user,
            action="contract_manual_update",
            object_type="ContractRecord",
            object_id=str(record.id),
            detail={"document": record.document_id},
        )

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        """Bestätigt Vertragsdaten und erledigt die offene Contract-Review."""
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        record = contract_service.confirm_contract(self.get_object(), actor=request.user)
        return Response(self.get_serializer(record).data)

    @action(detail=True, methods=["post"])
    def rescan(self, request, pk=None):
        """Scannt das verknüpfte Dokument erneut nach Vertragsdaten."""
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        record = self.get_object()
        result = contract_service.sync_contract_record(record.document, actor=request.user)
        record.refresh_from_db()
        return Response({"result": result, "contract": self.get_serializer(record).data})

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Kompakte Kennzahlen für das Vertrags-Cockpit."""
        today = timezone.now().date()
        cancel_until = today + timedelta(days=90)
        due_until = today + timedelta(days=30)
        qs = self.get_queryset()
        return Response(
            {
                "total": qs.count(),
                "active": qs.filter(status=ContractRecord.Status.ACTIVE).count(),
                "needs_review": qs.filter(needs_review=True).count(),
                "cancel_soon": qs.filter(
                    status=ContractRecord.Status.ACTIVE,
                    cancel_until__gte=today,
                    cancel_until__lte=cancel_until,
                ).count(),
                "due_soon": qs.filter(
                    status=ContractRecord.Status.ACTIVE,
                    next_due_on__gte=today,
                    next_due_on__lte=due_until,
                ).count(),
                "expired": qs.filter(status=ContractRecord.Status.EXPIRED).count(),
            }
        )

    @action(detail=False, methods=["get"], url_path="cost-overview")
    def cost_overview(self, request):
        """Fixkosten-/Ausgabenüberblick: monatliche/jährliche Summen + Aufschlüsselung."""
        try:
            upcoming_days = int(request.query_params.get("upcoming_days", 60))
        except (TypeError, ValueError):
            upcoming_days = 60
        upcoming_days = max(1, min(upcoming_days, 365))
        return Response(
            spending_service.cost_overview(self.get_queryset(), upcoming_days=upcoming_days)
        )

    @action(detail=False, methods=["post"])
    def scan(self, request):
        """Scannt sichtbare Dokumente nach Vertragsdaten.

        Ohne ``ids`` wird ein begrenzter Batch aktueller READY-Dokumente genutzt.
        Das macht den Endpunkt UI-tauglich und verhindert versehentliche
        Langläufer im Request-Thread; große Bestände können mehrfach gescannt
        oder später per Management Command/Celery ausgebaut werden.
        """
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        raw_limit = request.data.get("limit", 50) if hasattr(request, "data") else 50
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))

        qs = Document.objects.select_related(
            "current_version",
            "correspondent",
            "document_type",
            "case_file",
        ).exclude(current_version__isnull=True)
        if not getattr(request.user, "is_dms_admin", False):
            qs = qs.filter(owner=request.user)

        raw_ids = request.data.get("ids") if hasattr(request, "data") else None
        if raw_ids:
            if not isinstance(raw_ids, list):
                return Response(
                    {"detail": "Feld 'ids' muss eine Liste sein."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                ids = [int(raw_id) for raw_id in raw_ids]
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Feld 'ids' enthält ungültige Dokument-IDs."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(id__in=ids)
        else:
            qs = qs.filter(
                current_version__processing_state=DocumentVersion.ProcessingState.READY
            ).order_by("-added_at")[:limit]

        counters = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "no_contract": 0,
            "missing": 0,
            "failed": 0,
        }
        errors = []
        scanned = 0
        for document in qs:
            scanned += 1
            try:
                result = contract_service.sync_contract_record(
                    document, actor=request.user
                )
            except Exception as exc:  # noqa: BLE001 - Batch soll weiterlaufen
                counters["failed"] += 1
                errors.append({"document": document.id, "error": str(exc)[:500]})
                continue
            status_value = result.get("status", "failed")
            counters[status_value] = counters.get(status_value, 0) + 1

        return Response({"scanned": scanned, **counters, "errors": errors})


class KnowledgeEntityViewSet(viewsets.ModelViewSet):
    """Privates DMS-Gedächtnis: Personen, Firmen, Behörden und Identifier."""

    queryset = KnowledgeEntity.objects.all()
    serializer_class = KnowledgeEntitySerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        qs = (
            KnowledgeEntity.objects.select_related("owner")
            .prefetch_related("identifiers")
            .annotate(
                document_count=Count("document_links__document", distinct=True),
                relation_count=Count("outgoing_relations", distinct=True),
            )
            .order_by("kind", "name")
        )
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(owner=user)

        params = self.request.query_params
        kind = params.get("kind")
        if kind in {choice for choice, _label in KnowledgeEntity.Kind.choices}:
            qs = qs.filter(kind=kind)
        q = params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(canonical_name__icontains=q)
                | Q(identifiers__value__icontains=q)
                | Q(document_links__document__title__icontains=q)
            )
        document_id = params.get("document")
        if document_id:
            qs = qs.filter(document_links__document_id=document_id)
        return qs.distinct()

    def perform_create(self, serializer):
        kind = serializer.validated_data.get("kind")
        name = serializer.validated_data.get("name", "")
        serializer.save(
            owner=self.request.user,
            canonical_name=entity_graph_service.canonicalize(kind, name),
            source=KnowledgeEntity.Source.MANUAL,
            confidence=100,
        )

    def perform_update(self, serializer):
        kind = serializer.validated_data.get("kind", serializer.instance.kind)
        name = serializer.validated_data.get("name", serializer.instance.name)
        serializer.save(
            canonical_name=entity_graph_service.canonicalize(kind, name),
            source=KnowledgeEntity.Source.MANUAL,
        )

    @action(detail=True, methods=["get"])
    def documents(self, request, pk=None):
        entity = self.get_object()
        qs = Document.objects.filter(entity_links__entity=entity).select_related(
            "correspondent",
            "document_type",
            "folder",
            "case_file",
            "current_version",
        ).prefetch_related("tags", "versions", "review_tasks").distinct()
        if not getattr(request.user, "is_dms_admin", False):
            qs = qs.filter(owner=request.user)
        return Response(DocumentSerializer(qs, many=True).data)

    @action(detail=True, methods=["get"])
    def relations(self, request, pk=None):
        entity = self.get_object()
        qs = EntityRelation.objects.select_related(
            "from_entity", "to_entity", "document"
        ).filter(Q(from_entity=entity) | Q(to_entity=entity))
        if not getattr(request.user, "is_dms_admin", False):
            qs = qs.filter(
                Q(from_entity__owner=request.user) | Q(to_entity__owner=request.user)
            )
        return Response(EntityRelationSerializer(qs.distinct(), many=True).data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        qs = self.get_queryset()
        by_kind = {
            item["kind"]: item["count"]
            for item in qs.values("kind").annotate(count=Count("id"))
        }
        return Response(
            {
                "total": qs.count(),
                "by_kind": by_kind,
                "documents_linked": Document.objects.filter(
                    entity_links__entity__in=qs
                ).distinct().count(),
            }
        )

    @action(detail=False, methods=["post"])
    def scan(self, request):
        """Scannt sichtbare READY-Dokumente nach Entitäten."""
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        raw_limit = request.data.get("limit", 50) if hasattr(request, "data") else 50
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))

        qs = Document.objects.select_related(
            "current_version",
            "correspondent",
            "owner",
        ).exclude(current_version__isnull=True)
        if not getattr(request.user, "is_dms_admin", False):
            qs = qs.filter(owner=request.user)

        raw_ids = request.data.get("ids") if hasattr(request, "data") else None
        if raw_ids:
            if not isinstance(raw_ids, list):
                return Response(
                    {"detail": "Feld 'ids' muss eine Liste sein."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                ids = [int(raw_id) for raw_id in raw_ids]
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Feld 'ids' enthält ungültige Dokument-IDs."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(id__in=ids)
        else:
            qs = qs.filter(
                current_version__processing_state=DocumentVersion.ProcessingState.READY
            ).order_by("-added_at")[:limit]

        scanned = entities = links = relations = failed = 0
        errors = []
        for document in qs:
            scanned += 1
            try:
                result = entity_graph_service.sync_document_entities(
                    document, actor=request.user
                )
            except Exception as exc:  # noqa: BLE001 - Batch soll weiterlaufen
                failed += 1
                errors.append({"document": document.id, "error": str(exc)[:500]})
                continue
            entities += int(result.get("entities", 0))
            links += int(result.get("links", 0))
            relations += int(result.get("relations", 0))
        return Response(
            {
                "scanned": scanned,
                "entities": entities,
                "links": links,
                "relations": relations,
                "failed": failed,
                "errors": errors,
            }
        )


class DocumentEntityViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DocumentEntitySerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        qs = DocumentEntity.objects.select_related("document", "entity").order_by(
            "entity__kind", "entity__name"
        )
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(document__owner=user, entity__owner=user)
        document_id = self.request.query_params.get("document")
        if document_id:
            qs = qs.filter(document_id=document_id)
        entity_id = self.request.query_params.get("entity")
        if entity_id:
            qs = qs.filter(entity_id=entity_id)
        return qs


class EntityRelationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = EntityRelationSerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        qs = EntityRelation.objects.select_related(
            "from_entity", "to_entity", "document"
        ).order_by("relation_type", "from_entity__name")
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(
                Q(from_entity__owner=user) | Q(to_entity__owner=user)
            )
        entity_id = self.request.query_params.get("entity")
        if entity_id:
            qs = qs.filter(Q(from_entity_id=entity_id) | Q(to_entity_id=entity_id))
        document_id = self.request.query_params.get("document")
        if document_id:
            qs = qs.filter(document_id=document_id)
        return qs.distinct()


class TagViewSet(viewsets.ModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    permission_classes = [ReadCreateOrAdminMutate]


class CorrespondentViewSet(viewsets.ModelViewSet):
    queryset = Correspondent.objects.all()
    serializer_class = CorrespondentSerializer
    permission_classes = [ReadCreateOrAdminMutate]


class DocumentTypeViewSet(viewsets.ModelViewSet):
    queryset = DocumentType.objects.all()
    serializer_class = DocumentTypeSerializer
    permission_classes = [ReadCreateOrAdminMutate]


class StoragePathViewSet(viewsets.ModelViewSet):
    queryset = StoragePath.objects.all()
    serializer_class = StoragePathSerializer
    permission_classes = [ReadCreateOrAdminMutate]


class DocumentFolderViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentFolderSerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        user = self.request.user
        count_filter = None
        if not getattr(user, "is_dms_admin", False):
            count_filter = Q(documents__owner=user)
        return (
            DocumentFolder.objects.select_related("parent", "owner")
            .annotate(document_count=Count("documents", filter=count_filter))
            .order_by("parent__name", "name")
        )

    def perform_create(self, serializer):
        # Ersteller = Eigentümer (Sicherheits-Anker: nur er/Admin darf ändern).
        serializer.save(owner=self.request.user)

    def _assert_folder_mutable(self, instance):
        """Nur der Eigentümer (oder ein Admin) darf einen Ordner ändern/löschen.

        Der Ordnerbaum ist global lesbar (Navigation), aber Umbenennen, Verschieben
        (parent), Freigabe-Umschalten UND Löschen sind Owner-Aktionen. Sonst könnte
        ein Nutzer fremde Ordner umbenennen/verschieben oder – beim Löschen eines
        Elternordners – fremde Unterordner kaskadiert löschen und Dokumente ihrer
        Ordnerzuordnung berauben. Globale (ownerlose) Ordner sind admin-only.
        """
        user = self.request.user
        if getattr(user, "is_dms_admin", False):
            return
        if instance.owner_id != user.id:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "Nur der Ordner-Eigentümer (oder ein Admin) darf diesen Ordner "
                "ändern oder löschen. Globale Ordner sind admin-only."
            )

    def perform_update(self, serializer):
        self._assert_folder_mutable(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_folder_mutable(instance)
        instance.delete()


class SavedViewViewSet(viewsets.ModelViewSet):
    """Persönliche, gespeicherte Dokumentlisten-Ansichten."""

    serializer_class = SavedViewSerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        return SavedView.objects.filter(owner=self.request.user).order_by("name")

    def _save(self, serializer):
        with transaction.atomic():
            wants_default = serializer.validated_data.get(
                "is_default",
                getattr(serializer.instance, "is_default", False),
            )
            if wants_default:
                siblings = SavedView.objects.filter(
                    owner=self.request.user,
                    is_default=True,
                )
                if serializer.instance is not None:
                    siblings = siblings.exclude(pk=serializer.instance.pk)
                siblings.update(is_default=False)
            saved_view = serializer.save(owner=self.request.user)
        return saved_view

    def perform_create(self, serializer):
        self._save(serializer)

    def perform_update(self, serializer):
        self._save(serializer)


class CaseFileViewSet(viewsets.ModelViewSet):
    """Vorgangsakten: bündeln Dokumente zu einem fachlichen Vorgang."""

    serializer_class = CaseFileSerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        user = self.request.user
        qs = (
            CaseFile.objects.select_related("owner")
            .prefetch_related(
                "documents__correspondent",
                "documents__document_type",
                "documents__folder",
                "documents__current_version",
            )
            .annotate(
                document_count=Count("documents", distinct=True),
                latest_document_at=Max("documents__added_at"),
            )
        )
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(owner=user)

        status_value = self.request.query_params.get("status")
        if status_value in {choice for choice, _label in CaseFile.Status.choices}:
            qs = qs.filter(status=status_value)

        q = self.request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(description__icontains=q)
                | Q(documents__title__icontains=q)
                | Q(documents__current_version__ocr_text__icontains=q)
            )
        return qs.distinct().order_by("status", "-updated_at", "title")

    def perform_create(self, serializer):
        case_file = serializer.save(owner=self.request.user)
        AuditLogEntry.objects.create(
            actor=self.request.user,
            action="case_file_create",
            object_type="CaseFile",
            object_id=str(case_file.id),
            detail={"title": case_file.title},
        )

    def perform_update(self, serializer):
        before = {
            "title": serializer.instance.title,
            "description": serializer.instance.description,
            "status": serializer.instance.status,
        }
        super().perform_update(serializer)
        case_file = serializer.instance
        after = {
            "title": case_file.title,
            "description": case_file.description,
            "status": case_file.status,
        }
        changes = {
            key: {"from": before[key], "to": after[key]}
            for key in before
            if before[key] != after[key]
        }
        if changes:
            AuditLogEntry.objects.create(
                actor=self.request.user,
                action="case_file_update",
                object_type="CaseFile",
                object_id=str(case_file.id),
                detail={"changes": changes},
            )

    def _parse_document_ids(self, request):
        raw_ids = request.data.get("ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return None, Response(
                {"detail": "Feld 'ids' muss eine nicht-leere Liste sein."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ids = []
        for raw in raw_ids:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                return None, Response(
                    {"detail": f"Ungültige Dokument-ID: {raw!r}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return list(dict.fromkeys(ids)), None

    def _visible_documents(self, ids):
        qs = Document.objects.filter(id__in=ids)
        if not getattr(self.request.user, "is_dms_admin", False):
            qs = qs.filter(owner=self.request.user)
        return qs

    def _serialized(self, case_file):
        fresh = self.get_queryset().get(pk=case_file.pk)
        return self.get_serializer(fresh).data

    @action(detail=True, methods=["post"], url_path="add-documents")
    def add_documents(self, request, pk=None):
        """Ordnet sichtbare Dokumente dieser Akte zu."""
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        case_file = self.get_object()
        ids, error = self._parse_document_ids(request)
        if error is not None:
            return error
        documents = self._visible_documents(ids)
        assigned_ids = list(documents.values_list("id", flat=True))
        documents.update(case_file=case_file)
        AuditLogEntry.objects.create(
            actor=request.user,
            action="case_file_add_documents",
            object_type="CaseFile",
            object_id=str(case_file.id),
            detail={"document_ids": sorted(assigned_ids)},
        )
        return Response(self._serialized(case_file), status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="remove-documents")
    def remove_documents(self, request, pk=None):
        """Entfernt sichtbare Dokumente aus dieser Akte."""
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        case_file = self.get_object()
        ids, error = self._parse_document_ids(request)
        if error is not None:
            return error
        documents = self._visible_documents(ids).filter(case_file=case_file)
        removed_ids = list(documents.values_list("id", flat=True))
        documents.update(case_file=None)
        AuditLogEntry.objects.create(
            actor=request.user,
            action="case_file_remove_documents",
            object_type="CaseFile",
            object_id=str(case_file.id),
            detail={"document_ids": sorted(removed_ids)},
        )
        return Response(self._serialized(case_file), status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="summarize")
    def summarize(self, request, pk=None):
        """Erzeugt eine quellengebundene Zusammenfassung für die Akte."""
        if not getattr(request.user, "can_write", False):
            return Response(
                {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
                status=status.HTTP_403_FORBIDDEN,
            )
        case_file = self.get_object()
        from .services import case_files

        result = case_files.summarize_case_file(case_file)
        AuditLogEntry.objects.create(
            actor=request.user,
            action="case_file_summarize",
            object_type="CaseFile",
            object_id=str(case_file.id),
            detail={
                "source": result["source"],
                "sources": [source["document"] for source in result["sources"]],
            },
        )
        return Response(
            {
                "case_file": self._serialized(case_file),
                "summary": result["summary"],
                "source": result["source"],
                "sources": result["sources"],
            },
            status=status.HTTP_200_OK,
        )


class _OwnerScopedAutomationMixin:
    """Owner-Scoping für Automatisierungen (Workflows/Klassifizierungsregeln, P1).

    Nicht-Admins sehen globale (``owner=null``) UND eigene Objekte, dürfen aber nur
    EIGENE anlegen/ändern/löschen. Globale Automatisierungen (wirken auf ALLE
    Dokumente) verwalten ausschließlich Admins. So kann ein Haushaltsmitglied nicht
    über eine globale/fremde Regel fremde Dokumente verändern.
    """

    def _scope(self, qs):
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(Q(owner__isnull=True) | Q(owner=user))
        return qs

    def _assert_can_modify(self, instance):
        user = self.request.user
        if getattr(user, "is_dms_admin", False):
            return
        if instance.owner_id != user.id:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "Nur der Eigentümer (oder ein Admin) darf dies ändern/löschen; "
                "globale Automatisierungen sind admin-only."
            )

    def perform_create(self, serializer):
        user = self.request.user
        if getattr(user, "is_dms_admin", False):
            serializer.save()  # Admin: owner wie übergeben (Default null = global)
        else:
            serializer.save(owner=user)  # Nicht-Admin: erzwungen eigener Owner

    def perform_update(self, serializer):
        self._assert_can_modify(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_can_modify(instance)
        instance.delete()


class ClassificationRuleViewSet(_OwnerScopedAutomationMixin, viewsets.ModelViewSet):
    # queryset nur für die Router-Basename-Ableitung; das echte (owner-gescopte)
    # Queryset liefert get_queryset().
    queryset = ClassificationRule.objects.all()
    serializer_class = ClassificationRuleSerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        return self._scope(ClassificationRule.objects.all())

    def _simulation_documents(self, request):
        qs = (
            Document.objects.select_related(
                "correspondent",
                "document_type",
                "storage_path",
                "folder",
                "current_version",
            )
            .prefetch_related("tags")
            .all()
        )
        if not getattr(request.user, "is_dms_admin", False):
            qs = qs.filter(owner=request.user)
        return qs

    def _simulate_payload(self, request, *, rule=None):
        match = request.data.get("match") if isinstance(request.data, dict) else None
        then = request.data.get("then") if isinstance(request.data, dict) else None
        if rule is not None:
            match = match if isinstance(match, dict) else rule.match
            then = then if isinstance(then, dict) else rule.then
        if not isinstance(match, dict) or not isinstance(then, dict):
            return Response(
                {"detail": "Felder 'match' und 'then' muessen Objekte sein."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .services import rule_simulator

        return Response(
            rule_simulator.simulate_rule(
                match,
                then,
                self._simulation_documents(request),
            )
        )

    @action(detail=False, methods=["post"], url_path="simulate")
    def simulate_unsaved(self, request):
        """Simuliert einen Regelentwurf aus dem Formular ohne DB-Schreibvorgang."""
        return self._simulate_payload(request)

    @action(detail=True, methods=["post"], url_path="simulate")
    def simulate(self, request, pk=None):
        """Simuliert eine bestehende Regel gegen sichtbare Dokumente."""
        return self._simulate_payload(request, rule=self.get_object())


class CustomFieldViewSet(viewsets.ModelViewSet):
    """CRUD für Zusatzfeld-Definitionen (Spec §7.2).

    Löschen ist nur erlaubt, solange kein Dokument einen Wert für das Feld hat –
    sonst 409 mit klarer Meldung (verhindert stille Datenverluste). ``data_type``
    ist beim Update im Serializer eingefroren (Typwechsel wäre breaking).
    """

    queryset = CustomField.objects.all()
    serializer_class = CustomFieldSerializer
    # Globale Schema-Definition: Anlegen für Writer, Umbenennen/Löschen admin-only.
    permission_classes = [ReadCreateOrAdminMutate]

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if CustomFieldValue.objects.filter(field=instance).exists():
            return Response(
                {
                    "detail": (
                        "Zusatzfeld wird von mindestens einem Dokument verwendet "
                        "und kann nicht gelöscht werden. Entferne zuerst die Werte."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)


class WorkflowViewSet(_OwnerScopedAutomationMixin, viewsets.ModelViewSet):
    """CRUD für Workflows (STOAA-263) inkl. verschachteltem Trigger + Aktionen.

    Schreiben nur für ``can_write`` (nicht Gäste). Der Serializer nimmt
    ``trigger`` (Objekt) und ``actions`` (Liste) verschachtelt entgegen und
    ersetzt sie idempotent – passend zum geführten Frontend-Editor (PR3).
    """

    # queryset nur für die Router-Basename-Ableitung; siehe get_queryset().
    queryset = Workflow.objects.all()
    serializer_class = WorkflowSerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        return self._scope(
            Workflow.objects.prefetch_related(
                "trigger",
                "trigger__filter_has_tags",
                "trigger__filter_has_not_tags",
                "actions",
                "actions__assign_tags",
                "actions__remove_tags",
            ).all()
        )


class DocumentReminderViewSet(viewsets.ModelViewSet):
    """CRUD für Wiedervorlagen/Erinnerungen je Dokument (STOAA-372 PR1).

    Owner-Scope analog ``DocumentViewSet.get_queryset`` (STOAA-7): Ein Nutzer
    sieht/verwaltet ausschließlich Erinnerungen zu **eigenen** Dokumenten
    (``document__owner=user``); ``is_dms_admin`` sieht alles. Fremde IDs → 404
    (kein Leak, auch für Detail/Update/Delete/Actions, da ``get_object()`` das
    gefilterte Queryset nutzt).

    Extra-Endpunkte:
      * ``POST /api/reminders/{id}/done/`` – markiert erledigt (Audit
        ``reminder_done``).
      * ``GET  /api/reminders/due/?days=N`` – offene fällige/anstehende
        Erinnerungen, getrennt in ``{"faellig": [...], "anstehend": [...]}``.
    """

    queryset = DocumentReminder.objects.all()
    serializer_class = DocumentReminderSerializer
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        qs = DocumentReminder.objects.select_related("document", "created_by")
        # Owner-Isolation (STOAA-7): nur Erinnerungen zu eigenen Dokumenten.
        # DMS-Admins sehen/verwalten alles.
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(document__owner=user)
        return qs

    def perform_create(self, serializer):
        # Owner-Isolation in Schreibrichtung (STOAA-7): das Ziel-Dokument muss dem
        # Nutzer gehören (Admin darf alle). Sonst könnte man per POST eine
        # Erinnerung an ein FREMDES Dokument hängen. Fremd/unbekannt → 404 (kein
        # Leak, ob die ID existiert). Analog DocumentShareLinkViewSet.
        document = serializer.validated_data.get("document")
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            if document is None or document.owner_id != user.id:
                raise Http404("Dokument nicht gefunden.")
        # Der Ersteller kommt server-seitig aus dem Request (Serializer read-only),
        # damit er nicht fälschbar ist.
        reminder = serializer.save(created_by=self.request.user)
        AuditLogEntry.objects.create(
            actor=self.request.user,
            action="reminder_created",
            object_type="DocumentReminder",
            object_id=str(reminder.id),
            detail={
                "document": reminder.document_id,
                "remind_on": reminder.remind_on.isoformat(),
            },
        )

    @action(detail=True, methods=["post"])
    def done(self, request, pk=None):
        """Markiert die Erinnerung als erledigt (aus der Wiedervorlage genommen)."""
        reminder = self.get_object()
        if not reminder.done:
            reminder.done = True
            reminder.save(update_fields=["done", "updated_at"])
        AuditLogEntry.objects.create(
            actor=request.user,
            action="reminder_done",
            object_type="DocumentReminder",
            object_id=str(reminder.id),
            detail={"document": reminder.document_id},
        )
        return Response(self.get_serializer(reminder).data)

    @action(detail=False, methods=["get"])
    def due(self, request):
        """Offene fällige/anstehende Erinnerungen (In-App-Benachrichtigung).

        Query::

            GET /api/reminders/due/?days=N     (N optional, Default 7)

        Liefert nur **offene** (``done=False``) Erinnerungen des Nutzers
        (owner-gescopet über ``get_queryset``), getrennt::

            {
              "faellig":   [...],   # remind_on <= heute  (überfällig/heute)
              "anstehend": [...]    # heute < remind_on <= heute+N
            }
        """
        try:
            days = int(request.query_params.get("days", 7))
        except (TypeError, ValueError):
            days = 7
        if days < 0:
            days = 0

        today = timezone.localdate()
        horizon = today + timedelta(days=days)

        open_qs = self.get_queryset().filter(done=False)
        faellig = open_qs.filter(remind_on__lte=today)
        anstehend = open_qs.filter(remind_on__gt=today, remind_on__lte=horizon)

        return Response(
            {
                "faellig": self.get_serializer(faellig, many=True).data,
                "anstehend": self.get_serializer(anstehend, many=True).data,
            }
        )


class DocumentShareLinkViewSet(viewsets.ModelViewSet):
    """Verwaltungs-API für Freigabelinks (STOAA-190).

    Login-PFLICHT-Variante mit Pflicht-Ablauf. Endpunkte:

      * ``POST``   – Link erstellen (nur ``can_write``). Erzeugt einen Token via
        ``secrets.token_urlsafe(32)``, speichert **ausschließlich** dessen
        SHA-256-Hash und liefert den **Klartext-Token EINMALIG** in der Response
        (Feld ``token``). ``expires_at`` ist Pflicht und muss in der Zukunft
        liegen – fehlt es oder liegt es in der Vergangenheit → 400 (KEIN
        stillschweigendes „nie").
      * ``GET``    – Links auflisten (optional je Dokument via ``?document=<id>``)
        bzw. Detail – **ohne** ``token_hash``/Klartext.
      * ``DELETE`` / ``PATCH`` – Widerruf (Soft-Delete): setzt ``revoked_at`` auf
        jetzt → danach ``is_valid == False``. Der Datensatz bleibt für den
        Verlauf sichtbar.

    Owner-Scoping: Nutzer sehen/erstellen/widerrufen ausschließlich Links zu
    **eigenen** Dokumenten (DMS-Admin sieht alle). Gäste dürfen nur lesen.
    Die Abrufrouten ``/api/share/<token>/…`` sind NICHT Teil dieses Tickets.
    """

    queryset = DocumentShareLink.objects.all()  # für Basename-Ableitung im Router
    serializer_class = DocumentShareLinkSerializer
    permission_classes = [ReadOnlyOrCanWrite]
    # Voll-Ersatz (PUT) ist nicht sinnvoll; nur Widerruf via PATCH/DELETE.
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        # Owner-Scoping: nur Links zu eigenen Dokumenten (Admin sieht alles).
        # get_object() nutzt dieses Queryset → fremde IDs ergeben 404 (kein Leak).
        qs = DocumentShareLink.objects.select_related("document")
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(document__owner=user)
        doc_id = self.request.query_params.get("document")
        if doc_id:
            qs = qs.filter(document_id=doc_id)
        return qs

    def _get_owned_document(self, doc_id):
        """Lädt das Zieldokument owner-gescoped; fremd/unbekannt → 404 (kein Leak)."""
        from rest_framework.exceptions import ValidationError

        if not doc_id:
            raise ValidationError({"document": "Feld 'document' ist erforderlich."})
        qs = Document.objects.all()
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(owner=user)
        document = qs.filter(pk=doc_id).first()
        if document is None:
            # 404 statt 403: verrät nicht, ob eine fremde Dokument-ID existiert.
            raise Http404("Dokument nicht gefunden.")
        return document

    def _parse_future_expiry(self, raw):
        """Erzwingt Pflicht-Ablauf: vorhanden + gültiges ISO-Datum + in der Zukunft."""
        from rest_framework.exceptions import ValidationError
        from rest_framework.fields import DateTimeField as DRFDateTimeField

        if raw in (None, ""):
            raise ValidationError(
                {"expires_at": "Pflicht-Ablauf: 'expires_at' ist erforderlich."}
            )
        try:
            value = DRFDateTimeField().to_internal_value(raw)
        except ValidationError:
            raise ValidationError(
                {"expires_at": "Ungültiges Datumsformat (ISO 8601 erwartet)."}
            )
        if value <= timezone.now():
            raise ValidationError(
                {"expires_at": "'expires_at' muss in der Zukunft liegen."}
            )
        return value

    def create(self, request, *args, **kwargs):
        # Schreibrecht wird bereits durch ReadOnlyOrCanWrite erzwungen (403 für Gäste).
        document = self._get_owned_document(request.data.get("document"))
        expires_at = self._parse_future_expiry(request.data.get("expires_at"))

        # ≥32 Zeichen Entropie; nur der Hash landet in der DB.
        token = secrets.token_urlsafe(32)
        link = DocumentShareLink.objects.create(
            document=document,
            token_hash=DocumentShareLink.hash_token(token),
            expires_at=expires_at,
            created_by=request.user,
        )
        AuditLogEntry.objects.create(
            actor=request.user,
            action="share_link_create",
            object_type="Document",
            object_id=str(document.id),
            detail={"share_link_id": link.id, "expires_at": expires_at.isoformat()},
        )

        data = self.get_serializer(link).data
        # Klartext-Token EINMALIG – danach nie wieder abrufbar.
        data["token"] = token
        headers = self.get_success_headers(data)
        return Response(data, status=status.HTTP_201_CREATED, headers=headers)

    def _revoke(self, request, link):
        """Idempotenter Soft-Widerruf: setzt ``revoked_at`` genau einmal."""
        if link.revoked_at is None:
            link.revoked_at = timezone.now()
            link.save(update_fields=["revoked_at"])
            AuditLogEntry.objects.create(
                actor=request.user,
                action="share_link_revoke",
                object_type="Document",
                object_id=str(link.document_id),
                detail={"share_link_id": link.id},
            )
        return Response(self.get_serializer(link).data)

    def partial_update(self, request, *args, **kwargs):
        # Einzig unterstützte Änderung: Widerruf (revoked_at=now). Andere Felder
        # sind unveränderlich – ein Freigabelink wird widerrufen, nicht editiert.
        link = self.get_object()
        return self._revoke(request, link)

    def destroy(self, request, *args, **kwargs):
        # Soft-Delete: Widerruf statt Zeilenlöschung, damit der Link im Verlauf
        # sichtbar bleibt (is_valid=False). Antwort 200 mit dem widerrufenen Link.
        link = self.get_object()
        return self._revoke(request, link)


# ---------------------------------------------------------------------------
# Freigabe-Abrufrouten (STOAA-191) – /api/share/<token>/preview|download
# ---------------------------------------------------------------------------
def _resolve_valid_share_link(token: str):
    """Findet einen *gültigen* Freigabelink zu einem Klartext-Token.

    Einheitliche Rückgabe ``None`` für **alle** Nicht-Erfolgsfälle
    (unbekannt / widerrufen / abgelaufen), damit die API uniform mit
    ``410 Gone`` antwortet und keine Existenz-Enumeration ermöglicht.
    Verglichen wird ausschließlich der SHA-256-Hash – der Klartext wird
    nie gespeichert (siehe ``DocumentShareLink``).
    """
    if not token:
        return None
    link = (
        DocumentShareLink.objects.select_related(
            "document", "document__current_version"
        )
        .filter(token_hash=DocumentShareLink.hash_token(token))
        .first()
    )
    if link is None or not link.is_valid:
        return None
    return link


class _ShareAccessView(APIView):
    """Basis der Freigabe-Abrufrouten (Login-PFLICHT-Variante, STOAA-191).

    Sicherheitsmodell:
      * ``IsAuthenticated`` – ein Freigabelink ist **kein** anonymer Zugang;
        der Abrufende muss angemeldet sein (bewusste Login-Pflicht).
      * Der Link durchbricht die Owner-Isolation **ausschließlich** für das
        eine verknüpfte Dokument – keine Liste, keine Nachbardokumente.
      * ``410 Gone`` für unbekannte/widerrufene/abgelaufene Tokens (uniform,
        keine Existenz-Enumeration).
      * Jeder erfolgreiche Abruf wird auditiert (``AuditLogEntry``); die
        GoBD-Hash-Kette der Versionen bleibt unangetastet (reiner Lesezugriff).
    """

    permission_classes = [IsAuthenticated]

    #: Von den Unterklassen gesetzte Audit-Aktion.
    audit_action = ""

    def _serve(self, request, link):  # pragma: no cover - abstrakt
        raise NotImplementedError

    def get(self, request, token):
        link = _resolve_valid_share_link(token)
        if link is None:
            return Response(
                {"detail": "Freigabelink ist ungültig, widerrufen oder abgelaufen."},
                status=status.HTTP_410_GONE,
            )
        # _serve kann Http404 werfen (Datei fehlt) → dann KEIN Audit-Eintrag.
        response = self._serve(request, link)
        AuditLogEntry.objects.create(
            actor=request.user if request.user.is_authenticated else None,
            action=self.audit_action,
            object_type="Document",
            object_id=str(link.document_id),
            detail={"share_link_id": link.id},
        )
        return response


class SharePreviewView(_ShareAccessView):
    """Inline-Vorschau des freigegebenen Dokuments (``/api/share/<token>/preview``)."""

    audit_action = "share_preview"

    def _serve(self, request, link):
        version = link.document.current_version
        if version is None:
            raise Http404("Keine Version vorhanden.")
        return _serve_version_preview(version)


class ShareDownloadView(_ShareAccessView):
    """Download des freigegebenen Dokuments (``/api/share/<token>/download``)."""

    audit_action = "share_download"

    def _serve(self, request, link):
        version = link.document.current_version
        if version is None:
            raise Http404("Keine Version vorhanden.")
        return _serve_version_download(link.document, version)


class ProcessedMailViewSet(viewsets.ReadOnlyModelViewSet):
    """Mail-Center: verarbeitete IMAP-Mails ohne Zugangsdaten anzeigen.

    V1 ist bewusst read-mostly: Die eigentliche Ingestion bleibt in ``mail.py``.
    Hier werden Importstatus, Anhänge, verknüpfte Dokumente und einfache
    Bearbeitungsaktionen für die menschliche Nacharbeit sichtbar.
    """

    serializer_class = ProcessedMailSerializer
    permission_classes = [IsDmsAdmin]

    def get_queryset(self):
        qs = (
            ProcessedMail.objects.select_related("account")
            .prefetch_related(
                "documents__correspondent",
                "documents__document_type",
                "documents__folder",
                "documents__current_version",
            )
            .order_by("-processed_at", "-id")
        )

        status_value = self.request.query_params.get("status")
        if status_value in {choice for choice, _label in ProcessedMail.Status.choices}:
            qs = qs.filter(status=status_value)

        account = self.request.query_params.get("account")
        if account:
            qs = qs.filter(account_id=account)

        q = self.request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(subject__icontains=q)
                | Q(sender__icontains=q)
                | Q(message_id__icontains=q)
            )

        return qs

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """Kompakte Zähler für Statuskacheln im Mail-Center."""
        qs = self.get_queryset()
        by_status = {
            row["status"]: row["count"]
            for row in qs.values("status").annotate(count=Count("id"))
        }
        return Response(
            {
                "total": qs.count(),
                "imported": by_status.get(ProcessedMail.Status.IMPORTED, 0),
                "partial": by_status.get(ProcessedMail.Status.PARTIAL, 0),
                "ignored": by_status.get(ProcessedMail.Status.IGNORED, 0),
                "failed": by_status.get(ProcessedMail.Status.FAILED, 0),
                "attachments": qs.aggregate(total=Count("documents", distinct=True))[
                    "total"
                ],
            }
        )

    @action(detail=True, methods=["post"], url_path="mark-ignored")
    def mark_ignored(self, request, pk=None):
        """Markiert eine Mail als fachlich erledigt/ignoriert."""
        item = self.get_object()
        note = str(request.data.get("note", "") or "").strip()
        item.status = ProcessedMail.Status.IGNORED
        if note:
            item.note = note[:4000]
        item.save(update_fields=["status", "note"])
        AuditLogEntry.objects.create(
            actor=request.user,
            action="processed_mail_ignored",
            object_type="ProcessedMail",
            object_id=str(item.id),
            detail={"note": item.note},
        )
        return Response(self.get_serializer(item).data)


class MailAccountViewSet(viewsets.ModelViewSet):
    """Verwaltung der IMAP-Postfächer (STOAA-212).

    Nur DMS-Admins (``IsDmsAdmin``): IMAP-Zugangsdaten sind sensibel. Das
    Passwort wird write-only entgegengenommen, at-rest verschlüsselt (Model-
    ``save()``) und nie ausgegeben. Zusätzlich ein Verbindungstest-Endpoint,
    der Zugangsdaten prüft, ohne sie zu speichern.
    """

    queryset = MailAccount.objects.all()
    serializer_class = MailAccountSerializer
    permission_classes = [IsDmsAdmin]

    def _audit(self, action, account_id, detail=None):
        AuditLogEntry.objects.create(
            actor=self.request.user,
            action=action,
            object_type="MailAccount",
            object_id=str(account_id),
            detail=detail or {},
        )

    def perform_create(self, serializer):
        account = serializer.save()
        self._audit("mailaccount_create", account.id, {"name": account.name})

    def perform_update(self, serializer):
        account = serializer.save()
        self._audit("mailaccount_update", account.id, {"name": account.name})

    def perform_destroy(self, instance):
        account_id, name = instance.id, instance.name
        instance.delete()
        self._audit("mailaccount_delete", account_id, {"name": name})

    def _run_connection_test(self, account, *, persist):
        """IMAP-Verbindung testen und ``(ok, message)`` liefern.

        ``persist=True`` (nur bei gespeicherten Konten): Ergebnis am Konto
        festhalten – ``last_checked_at`` = jetzt, ``last_error`` = "" bei Erfolg
        bzw. die Fehlermeldung. So bleibt der Status nach Reload/Seitenwechsel
        erhalten (STOAA-172: „Banner grün, ``last_checked_at`` aktualisiert").
        """
        from django.utils import timezone

        from .mail import connect

        try:
            conn = connect(account)
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 – Logout-Fehler nach Erfolg ignorieren
                pass
            ok, message = True, "Verbindung erfolgreich."
        except Exception as exc:  # noqa: BLE001 – jede IMAP-/Netzwerkstörung melden
            ok, message = False, str(exc) or exc.__class__.__name__

        if persist:
            account.last_checked_at = timezone.now()
            account.last_error = "" if ok else message
            account.save(update_fields=["last_checked_at", "last_error"])
            self._audit("mailaccount_test_connection", account.id, {"ok": ok})

        return ok, message

    @action(detail=False, methods=["post"], url_path="test-connection")
    def test_connection(self, request):
        """IMAP-Verbindung mit übergebenen (oder gespeicherten) Zugangsdaten testen.

        Body: entweder ``{"id": <pk>}`` (testet ein gespeichertes Konto) **oder**
        vollständige Zugangsdaten ``{host, port, use_ssl, username, password}``
        (bzw. ``password_env``).

        Persistierung (STOAA-172): Wird ein **gespeichertes** Konto getestet
        (``id`` gesetzt bzw. Detail-Route ``/{pk}/test-connection/``), werden
        ``last_checked_at`` / ``last_error`` am Konto aktualisiert. Ein Test mit
        rohen Zugangsdaten (Anlege-Formular, noch kein Konto) bleibt zustandslos.

        Antwort: ``{"ok": bool, "message": str}`` (HTTP 200 – ein fehlgeschlagener
        Test ist kein Client-Fehler, sondern ein erwartetes Ergebnis).
        """
        data = request.data
        account_id = data.get("id")
        if account_id is not None:
            account = self.get_queryset().filter(pk=account_id).first()
            if account is None:
                return Response(
                    {"detail": "Konto nicht gefunden."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            persist = True
        else:
            host = (data.get("host") or "").strip()
            username = (data.get("username") or "").strip()
            if not host or not username:
                return Response(
                    {"detail": "host und username sind erforderlich."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Transientes (nicht gespeichertes) Konto nur für den Verbindungstest.
            account = MailAccount(
                name="__test__",
                host=host,
                port=int(data.get("port") or 993),
                use_ssl=bool(data.get("use_ssl", True)),
                username=username,
                folder=data.get("folder") or "INBOX",
                password=data.get("password") or "",
                password_env=data.get("password_env") or "",
            )
            persist = False

        ok, message = self._run_connection_test(account, persist=persist)
        return Response({"ok": ok, "message": message})

    @action(detail=True, methods=["post"], url_path="test-connection")
    def test_connection_detail(self, request, pk=None):
        """Verbindungstest für ein gespeichertes Konto (``/{pk}/test-connection/``).

        Spec-konforme Detail-Route (STOAA-172): testet das adressierte Konto und
        persistiert ``last_checked_at`` / ``last_error``. Alias-Bequemlichkeit zur
        Collection-Route mit ``{"id": pk}``.
        """
        account = self.get_object()
        ok, message = self._run_connection_test(account, persist=True)
        return Response({"ok": ok, "message": message})
