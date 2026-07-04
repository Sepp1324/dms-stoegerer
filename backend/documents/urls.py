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

urlpatterns = [
    # Explizit vor dem Router, sonst würde "upload" als Dokument-PK gelesen.
    path("documents/upload/", views.DocumentUploadView.as_view(), name="document-upload"),
    *router.urls,
]
