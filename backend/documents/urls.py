from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("documents", views.DocumentViewSet)
router.register("tags", views.TagViewSet)
router.register("correspondents", views.CorrespondentViewSet)
router.register("document-types", views.DocumentTypeViewSet)
router.register("storage-paths", views.StoragePathViewSet)
router.register("folders", views.DocumentFolderViewSet, basename="folder")
router.register("saved-views", views.SavedViewViewSet, basename="saved-view")
router.register("case-files", views.CaseFileViewSet, basename="case-file")
router.register("classification-rules", views.ClassificationRuleViewSet)
router.register("custom-fields", views.CustomFieldViewSet)
router.register("document-share-links", views.DocumentShareLinkViewSet)
router.register("processed-mails", views.ProcessedMailViewSet, basename="processed-mail")
router.register("mail-accounts", views.MailAccountViewSet)
router.register("workflows", views.WorkflowViewSet)
router.register("reminders", views.DocumentReminderViewSet)
router.register("review-tasks", views.DocumentReviewTaskViewSet, basename="review-task")
router.register("dossiers", views.DossierViewSet, basename="dossier")
router.register("contracts", views.ContractRecordViewSet, basename="contract")
router.register("knowledge-entities", views.KnowledgeEntityViewSet, basename="knowledge-entity")
router.register("document-entities", views.DocumentEntityViewSet, basename="document-entity")
router.register("entity-relations", views.EntityRelationViewSet, basename="entity-relation")

urlpatterns = [
    # Explizit vor dem Router, sonst würde "upload" als Dokument-PK gelesen.
    path("documents/upload/", views.DocumentUploadView.as_view(), name="document-upload"),
    path("system/backup-status/", views.BackupStatusView.as_view(), name="backup-status"),
    path(
        "system/semantic-index/",
        views.SemanticIndexHealthView.as_view(),
        name="semantic-index-health",
    ),
    path("system/archive-health/", views.ArchiveHealthView.as_view(), name="archive-health"),
    path("system/ocr-health/", views.OCRHealthView.as_view(), name="ocr-health"),
    path("timeline/", views.TimelineView.as_view(), name="timeline"),
    path("timeline/ics/", views.TimelineICSView.as_view(), name="timeline-ics"),
    path("ask/", views.AskView.as_view(), name="ask"),
    path("search/semantic/", views.SemanticSearchView.as_view(), name="search-semantic"),
    path("search/hybrid/", views.HybridSearchView.as_view(), name="search-hybrid"),
    path(
        "system/ocr-health/retry-failed/",
        views.OCRRetryFailedView.as_view(),
        name="ocr-health-retry-failed",
    ),
    # Mobile-Erfassung: mehrere Bilder → ein PDF (STOAA-513). Explizit vor dem
    # Router, sonst würde "mobile-capture" als Dokument-PK gelesen.
    path(
        "documents/mobile-capture/",
        views.MobileCaptureUploadView.as_view(),
        name="document-mobile-capture",
    ),
    # Freigabe-Abrufrouten (STOAA-191). Bewusst OHNE Trailing-Slash (exakt wie im
    # Ticket), damit kein APPEND_SLASH-Redirect den Authorization-Header verwirft.
    path("share/<str:token>/preview", views.SharePreviewView.as_view(), name="share-preview"),
    path("share/<str:token>/download", views.ShareDownloadView.as_view(), name="share-download"),
    *router.urls,
]
