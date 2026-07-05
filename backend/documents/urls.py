from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("documents", views.DocumentViewSet)
router.register("tags", views.TagViewSet)
router.register("correspondents", views.CorrespondentViewSet)
router.register("document-types", views.DocumentTypeViewSet)
router.register("storage-paths", views.StoragePathViewSet)
router.register("classification-rules", views.ClassificationRuleViewSet)
router.register("custom-fields", views.CustomFieldViewSet)
router.register("document-share-links", views.DocumentShareLinkViewSet)
router.register("mail-accounts", views.MailAccountViewSet)
router.register("workflows", views.WorkflowViewSet)
router.register("reminders", views.DocumentReminderViewSet)

urlpatterns = [
    # Explizit vor dem Router, sonst würde "upload" als Dokument-PK gelesen.
    path("documents/upload/", views.DocumentUploadView.as_view(), name="document-upload"),
    # Freigabe-Abrufrouten (STOAA-191). Bewusst OHNE Trailing-Slash (exakt wie im
    # Ticket), damit kein APPEND_SLASH-Redirect den Authorization-Header verwirft.
    path("share/<str:token>/preview", views.SharePreviewView.as_view(), name="share-preview"),
    path("share/<str:token>/download", views.ShareDownloadView.as_view(), name="share-download"),
    *router.urls,
]
