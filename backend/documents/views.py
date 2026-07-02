import os

from django.db import connection
from django.http import FileResponse, Http404
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
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
from .models import Correspondent, Document, DocumentType, StoragePath, Tag
from .serializers import (
    CorrespondentSerializer,
    DocumentSerializer,
    DocumentTypeSerializer,
    StoragePathSerializer,
    TagSerializer,
)
from .tasks import process_document_version


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


class DocumentViewSet(viewsets.ModelViewSet):
    """Dokumente auflisten/abrufen inkl. Volltextsuche und Filtern.

    Query-Parameter der Liste:
      * ``q``             – Volltextsuche über Titel + OCR-Text (PostgreSQL FTS)
      * ``correspondent`` – Filter auf Korrespondenten-ID
      * ``document_type`` – Filter auf Dokumenttyp-ID
      * ``tag``           – Filter auf Tag-ID
    """

    serializer_class = DocumentSerializer
    queryset = Document.objects.all()  # für Basename-Ableitung im Router
    permission_classes = [ReadOnlyOrCanWrite]

    def get_queryset(self):
        qs = (
            Document.objects.all()
            .select_related("correspondent", "document_type", "current_version")
            .prefetch_related("tags", "versions")
        )
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
        if params.get("tag"):
            qs = qs.filter(tags__id=params["tag"])

        return qs.distinct()

    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        """Liefert das Archiv-PDF der aktuellen Version zur Inline-Vorschau.

        Fällt auf das Original zurück, falls (noch) kein OCR-Archiv existiert.
        Der Pfad stammt aus der DB (nicht aus Nutzereingaben) – keine Traversal-Gefahr.
        """
        document = self.get_object()
        version = document.current_version
        if version is None:
            raise Http404("Keine Version vorhanden.")

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
