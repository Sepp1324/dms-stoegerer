import os
import re
import secrets
from datetime import date as date_cls
from datetime import datetime, time
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import connection
from django.db.models import Case, DecimalField, Q, Value, When
from django.db.models.functions import Cast
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
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

from . import pipeline, storage
from .models import (
    AuditLogEntry,
    ClassificationRule,
    Correspondent,
    CustomField,
    CustomFieldValue,
    Document,
    DocumentShareLink,
    DocumentType,
    StoragePath,
    Tag,
)
from .serializers import (
    AuditLogEntrySerializer,
    ClassificationRuleSerializer,
    CorrespondentSerializer,
    CustomFieldSerializer,
    DocumentSerializer,
    DocumentShareLinkSerializer,
    DocumentTypeSerializer,
    StoragePathSerializer,
    TagSerializer,
)
from .tasks import process_document_version

# Erkennt Bereichsfilter auf Zusatzfeldern: ``custom_field_<id>_gte`` / ``_lte``.
_CUSTOM_FIELD_PARAM_RE = re.compile(r"^custom_field_(\d+)_(gte|lte)$")
# Werte, die sich verlustfrei zu DECIMAL casten lassen (Vorzeichen + Dezimalpunkt).
# Andere ``CustomFieldValue.value`` (Text/Datum/„k. A.") werden per CASE zu NULL
# und fallen aus dem Vergleich – kein Postgres-500 beim Cast.
_NUMERIC_VALUE_RE = r"^-?[0-9]+(\.[0-9]+)?$"
_DECIMAL_OUTPUT = DecimalField(max_digits=30, decimal_places=10)


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
            "version": "0.1.0",
            "database": "ok" if db_ok else "unreachable",
        },
        status=200 if db_ok else 503,
    )


class DocumentUploadView(APIView):
    """Nimmt eine Datei per multipart/form-data auf und stößt die Pipeline an.

    Felder: ``file`` (Pflicht), ``title`` (optional; Standard = Dateiname).
    Antwortet mit dem angelegten Dokument; OCR läuft asynchron im Worker.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

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
        file_path, size, mime = storage.save_upload(uploaded)

        document, version = pipeline.create_document_from_file(
            file_path, title=title, owner=request.user, mime=mime, size=size
        )
        # OCR/Hash-Kette asynchron im Celery-Worker.
        process_document_version.delay(version.id)

        return Response(
            DocumentSerializer(document).data,
            status=status.HTTP_201_CREATED,
        )


def _serve_version_preview(version):
    """Liefert das Archiv-PDF (Original als Fallback) einer Version inline.

    Der Pfad stammt ausschließlich aus der DB (nie aus Nutzereingaben) –
    keine Traversal-Gefahr. Gemeinsam genutzt von ``DocumentViewSet.preview``
    und den Freigabe-Abrufrouten (STOAA-191), damit beide Pfade identisch
    liefern.
    """
    path = version.archive_path or version.file_path
    if not path or not os.path.exists(path):
        raise Http404("Datei nicht gefunden.")
    content_type = (
        "application/pdf"
        if version.archive_path
        else (version.mime_type or "application/octet-stream")
    )
    # as_attachment=False → inline anzeigen (PDF-Vorschau im Browser)
    return FileResponse(open(path, "rb"), content_type=content_type)


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
    return FileResponse(
        open(path, "rb"),
        as_attachment=True,
        filename=filename,
        content_type=version.mime_type or "application/octet-stream",
    )


class DocumentViewSet(viewsets.ModelViewSet):
    """Dokumente auflisten/abrufen inkl. Volltextsuche und Filtern.

    Query-Parameter der Liste:
      * ``q``             – Volltextsuche über Titel + OCR-Text (PostgreSQL FTS)
      * ``correspondent`` – Filter auf Korrespondenten-ID
      * ``document_type`` – Filter auf Dokumenttyp-ID
      * ``storage_path``  – Filter auf Speicherpfad-ID
      * ``tag``           – Filter auf Tag-ID (mehrfach angebbar → ODER-Verknüpfung,
                            z. B. ``?tag=1&tag=2``)
      * ``ordering``      – Sortierung, z. B. ``added_at``/``-added_at`` (Datum)
                            oder ``title``/``-title`` (A–Z). Ohne Angabe gilt die
                            Standard-Sortierung (bei ``q`` nach FTS-Relevanz,
                            sonst ``-added_at`` aus ``Document.Meta.ordering``).
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

    def get_queryset(self):
        qs = (
            Document.objects.all()
            .select_related(
                "correspondent", "document_type", "storage_path", "current_version"
            )
            .prefetch_related(
                "tags", "versions", "custom_field_values__field"
            )
        )
        # --- Owner-Isolation (STOAA-7) -------------------------------------
        # Jeder Nutzer sieht/verwaltet ausschließlich eigene Dokumente. Da
        # get_object() dieses Queryset nutzt, wirkt die Scope-Filterung auch
        # auf Detail/Download/Update/Delete sowie die Sub-Actions (preview,
        # thumbnail, audit, apply_suggestions): fremde IDs → 404 (kein Leak).
        # Ausnahme: DMS-Admin (Rolle admin / superuser) verwaltet alles.
        user = self.request.user
        if not getattr(user, "is_dms_admin", False):
            qs = qs.filter(owner=user)

        params = self.request.query_params

        q = params.get("q", "").strip()
        if q:
            from django.contrib.postgres.search import (
                SearchQuery,
                SearchRank,
                SearchVector,
            )

            vector = SearchVector("title", weight="A", config="german") + SearchVector(
                "current_version__ocr_text", weight="B", config="german"
            )
            query = SearchQuery(q, config="german")
            qs = (
                qs.annotate(rank=SearchRank(vector, query))
                .filter(rank__gt=0)
                .order_by("-rank", "-added_at")
            )

        if params.get("correspondent"):
            qs = qs.filter(correspondent_id=params["correspondent"])
        if params.get("document_type"):
            qs = qs.filter(document_type_id=params["document_type"])
        if params.get("storage_path"):
            qs = qs.filter(storage_path_id=params["storage_path"])
        # ``tag`` mehrfach erlaubt (?tag=1&tag=2) → ODER via ``__in``;
        # ein einzelner Wert bleibt abwärtskompatibel (getlist → ["1"]).
        tags = params.getlist("tag")
        if tags:
            qs = qs.filter(tags__id__in=tags)

        # Zusatzfeld-Bereichsfilter (Spec §7.3): custom_field_<id>_gte/_lte
        qs = _apply_custom_field_filters(qs, params)

        return qs.distinct()

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

    @action(detail=True, methods=["get"])
    def integrity(self, request, pk=None):
        """Prüft die Hash-Kette des Dokuments (Datei-Hash + prev_hash-Verkettung).

        Nur-Lesen – auch für Gäste. Rechnet die Datei-Hashes frisch nach.
        """
        document = self.get_object()
        return Response(pipeline.verify_document_integrity(document))

    @action(
        detail=True,
        methods=["post"],
        parser_classes=[MultiPartParser, FormParser],
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

        file_path, size, mime = storage.save_upload(uploaded)
        version = pipeline.create_version_for_document(
            document, file_path, created_by=request.user, mime=mime, size=size
        )
        # OCR/Hash-Kette asynchron im Celery-Worker.
        process_document_version.delay(version.id)

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

        return FileResponse(open(path, "rb"), content_type="image/jpeg")

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
            "tags": sorted(t.name for t in document.tags.all()),
        }

    def perform_update(self, serializer):
        """Speichert und protokolliert geänderte Metadatenfelder (vorher/nachher)."""
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

    def perform_destroy(self, instance):
        """Protokolliert die Löschung, bevor das Dokument entfernt wird.

        Audit-Einträge referenzieren die ID als String (keine FK) und überleben
        die Löschung des Dokuments – das Protokoll bleibt append-only lückenlos.
        """
        from django.core.exceptions import ValidationError as DjValidationError
        from rest_framework.exceptions import PermissionDenied

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

        return Response(self.get_serializer(document).data)

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


class TagViewSet(viewsets.ModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    permission_classes = [ReadOnlyOrCanWrite]


class CorrespondentViewSet(viewsets.ModelViewSet):
    queryset = Correspondent.objects.all()
    serializer_class = CorrespondentSerializer
    permission_classes = [ReadOnlyOrCanWrite]


class DocumentTypeViewSet(viewsets.ModelViewSet):
    queryset = DocumentType.objects.all()
    serializer_class = DocumentTypeSerializer
    permission_classes = [ReadOnlyOrCanWrite]


class StoragePathViewSet(viewsets.ModelViewSet):
    queryset = StoragePath.objects.all()
    serializer_class = StoragePathSerializer
    permission_classes = [ReadOnlyOrCanWrite]


class ClassificationRuleViewSet(viewsets.ModelViewSet):
    queryset = ClassificationRule.objects.all()
    serializer_class = ClassificationRuleSerializer
    permission_classes = [ReadOnlyOrCanWrite]


class CustomFieldViewSet(viewsets.ModelViewSet):
    """CRUD für Zusatzfeld-Definitionen (Spec §7.2).

    Löschen ist nur erlaubt, solange kein Dokument einen Wert für das Feld hat –
    sonst 409 mit klarer Meldung (verhindert stille Datenverluste). ``data_type``
    ist beim Update im Serializer eingefroren (Typwechsel wäre breaking).
    """

    queryset = CustomField.objects.all()
    serializer_class = CustomFieldSerializer
    permission_classes = [ReadOnlyOrCanWrite]

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
