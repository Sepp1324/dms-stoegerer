"""Zentrale URL-Konfiguration."""
from django.contrib import admin
from django.urls import include, path
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
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


class LogoutView(APIView):
    """Serverseitiger Logout (P2): blacklistet den übergebenen Refresh-Token, damit
    ein kopierter Token nach dem Logout NICHT mehr refreshbar ist (das Frontend
    löscht ihn zusätzlich lokal). Idempotent: ein ungültiger/abgelaufener/fehlender
    Token liefert trotzdem 205 – kein Info-Leak, ob der Token gültig war. Gedrosselt
    wie der Refresh, damit die Blacklist-Tabelle nicht geflutet werden kann."""

    permission_classes = [AllowAny]
    throttle_classes = [TokenRefreshRateThrottle]

    def post(self, request):
        refresh = request.data.get("refresh") if hasattr(request, "data") else None
        if refresh:
            try:
                RefreshToken(refresh).blacklist()
            except TokenError:
                pass  # ungültig/abgelaufen -> egal (idempotent)
        return Response(status=status.HTTP_205_RESET_CONTENT)


urlpatterns = [
    path("admin/", admin.site.urls),
    # Readiness (inkl. DB) – vom Frontend und k8s genutzt
    path("api/health/", health, name="health"),
    # Liveness (NUR Webprozess, KEINE DB) – k8s livenessProbe
    path("api/livez/", livez, name="livez"),
    # Auth (drosselt Login/Refresh gegen Brute-Force)
    path("api/auth/token/", ThrottledTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", ThrottledTokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/logout/", LogoutView.as_view(), name="token_logout"),
    # App-APIs
    path("api/", include("accounts.urls")),
    path("api/", include("documents.urls")),
]
