"""Zentrale URL-Konfiguration."""
from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from documents.views import health, livez

urlpatterns = [
    path("admin/", admin.site.urls),
    # Readiness (inkl. DB) – vom Frontend und k8s genutzt
    path("api/health/", health, name="health"),
    # Liveness (NUR Webprozess, KEINE DB) – k8s livenessProbe
    path("api/livez/", livez, name="livez"),
    # Auth
    path("api/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # App-APIs
    path("api/", include("accounts.urls")),
    path("api/", include("documents.urls")),
]
