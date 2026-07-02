from django.db import connection
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import pipeline, storage
from .models import Correspondent, Document, DocumentType, Tag
from .serializers import (
    CorrespondentSerializer,
    DocumentSerializer,
    DocumentTypeSerializer,
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


class TagViewSet(viewsets.ModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer


class CorrespondentViewSet(viewsets.ModelViewSet):
    queryset = Correspondent.objects.all()
    serializer_class = CorrespondentSerializer


class DocumentTypeViewSet(viewsets.ModelViewSet):
    queryset = DocumentType.objects.all()
    serializer_class = DocumentTypeSerializer
