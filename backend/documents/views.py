import os
import re
import secrets
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Case, DecimalField, Q, Value, When
from django.db.models.functions import Cast
from django.http import FileResponse, Http404, HttpResponse
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
from .services import version_compare
from .models import (
    AuditLogEntry,
    ClassificationRule,
    Correspondent,
    CustomField,
    CustomFieldValue,
    Document,
    DocumentReminder,
    DocumentShareLink,
    DocumentType,
    DocumentVersion,
    MailAccount,
    StoragePath,
    Tag,
    Workflow,
)
from .serializers import (
    AuditLogEntrySerializer,
    ClassificationRuleSerializer,
    CorrespondentSerializer,
    CustomFieldSerializer,
    DocumentReminderSerializer,
    DocumentSerializer,
    DocumentShareLinkSerializer,
    DocumentTypeSerializer,
    DocumentVersionSerializer,
    MailAccountSerializer,
    StoragePathSerializer,
    TagSerializer,
    WorkflowSerializer,
)
from .services import asn as asn_service
from .tasks import (
    bulk_classify_documents,
    process_document_version,
    retry_document_version,
)

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
            from django.contrib.postgres.search import (
                SearchQuery,
                SearchRank,
                SearchVector,
            )

            # Gewichteter Vektor (STOAA-256): Titel/Korrespondent (A) ranken vor
            # Dokumenttyp/Tags/Mail-Feldern (B), OCR-Fließtext (D) am schwächsten.
            # Query-Zeit-Vektor (keine materialisierte Spalte/GIN) – bewusst, da
            # der Vektor Join-Tabellen spannt; performant für Familien-Korpus.
            # Known-Limitation: PostgreSQL-FTS tokenisiert reine E-Mail-Adressen
            # als EIN atomares Token → Teilstrings der Sender-Domain (z. B. nur
            # "energieanbieter") sind nicht als Lexeme suchbar. Der From-Header
            # wird von mail.py als "Anzeigename <adresse>" gespeichert, sodass
            # über den Anzeigenamen gesucht werden kann. Substring-Domainsuche
            # bei anzeigenamenlosen Absendern ist ein optionales Folge-Ticket.
            vector = (
                SearchVector("title", weight="A", config="german")
                + SearchVector("correspondent__name", weight="A", config="german")
                + SearchVector("document_type__name", weight="B", config="german")
                + SearchVector("tags__name", weight="B", config="german")
                + SearchVector("mail_subject", weight="B", config="german")
                + SearchVector("mail_sender", weight="B", config="german")
                + SearchVector(
                    "current_version__ocr_text", weight="D", config="german"
                )
            )
            query = SearchQuery(q, config="german")
            qs = (
                qs.annotate(rank=SearchRank(vector, query))
                .filter(rank__gt=0)
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
        Schlägt der konfigurierte Provider beim Aufruf fehl (falscher Key/Modell,
        Netzwerk), ist ``source`` ``"error"`` mit knapper ``error``-Ursache – so
        verpufft ein Fehlkonfig nicht mehr als 500.
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
        payload = {**data, "source": result.get("source", "unavailable")}
        # Bei source="error" die knappe Ursache mitgeben (kein Secret/Stacktrace).
        if result.get("error"):
            payload["error"] = result["error"]
        return Response(payload)

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


class WorkflowViewSet(viewsets.ModelViewSet):
    """CRUD für Workflows (STOAA-263) inkl. verschachteltem Trigger + Aktionen.

    Schreiben nur für ``can_write`` (nicht Gäste). Der Serializer nimmt
    ``trigger`` (Objekt) und ``actions`` (Liste) verschachtelt entgegen und
    ersetzt sie idempotent – passend zum geführten Frontend-Editor (PR3).
    """

    queryset = Workflow.objects.prefetch_related(
        "trigger",
        "trigger__filter_has_tags",
        "trigger__filter_has_not_tags",
        "actions",
        "actions__assign_tags",
        "actions__remove_tags",
    ).all()
    serializer_class = WorkflowSerializer
    permission_classes = [ReadOnlyOrCanWrite]


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
