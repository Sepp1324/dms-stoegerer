"""Zentrale URL-Konfiguration."""
from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from documents.throttling import LoginRateThrottle, TokenRefreshRateThrottle
from documents.views import health, livez


# Rate-Limit gegen Brute-Force (P1): die simplejwt-Views sind ohne Drossel und
# damit auf einer öffentlichen Instanz praktisch ungebremst durchprobierbar.
class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [LoginRateThrottle]


class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_classes = [TokenRefreshRateThrottle]


urlpatterns = [
    path("admin/", admin.site.urls),
    # Readiness (inkl. DB) – vom Frontend und k8s genutzt
    path("api/health/", health, name="health"),
    # Liveness (NUR Webprozess, KEINE DB) – k8s livenessProbe
    path("api/livez/", livez, name="livez"),
    # Auth (drosselt Login/Refresh gegen Brute-Force)
    path("api/auth/token/", ThrottledTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", ThrottledTokenRefreshView.as_view(), name="token_refresh"),
    # App-APIs
    path("api/", include("accounts.urls")),
    path("api/", include("documents.urls")),
]
