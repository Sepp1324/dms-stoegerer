"""Rate-Limits für die Upload-Pfade (P1, DoS-Schutz).

Feste, per-Nutzer gescopte Drosseln – bewusst als ``SimpleRateThrottle``-
Subklassen mit hartem ``scope`` statt ``ScopedRateThrottle`` (dessen Scope aus
``view.throttle_scope`` gelesen wird und bei ViewSet-Actions unzuverlässig
greift). Die Raten stehen in ``settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']``
(env-tunebar) und die Zähler liegen im Django-Cache (in Produktion Redis, damit
das Limit über alle Pods GEMEINSAM gilt – siehe ``settings.CACHE_URL``).
"""
from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle


class _PerUserScopeThrottle(SimpleRateThrottle):
    """Drossel mit festem Scope, gekeyt auf den authentifizierten Nutzer."""

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            ident = user.pk
        else:
            ident = self.get_ident(request)  # Fallback: Client-IP
        return self.cache_format % {"scope": self.scope, "ident": ident}


class UploadRateThrottle(_PerUserScopeThrottle):
    """Limit für Dokument-Upload und neue Versionen (Scope ``upload``)."""

    scope = "upload"


class CaptureRateThrottle(_PerUserScopeThrottle):
    """Limit für den Mobile-Capture-Upload (Scope ``capture``)."""

    scope = "capture"


class AiRateThrottle(_PerUserScopeThrottle):
    """Limit für teure KI-Endpunkte – Copilot (Ask) und semantische Suche
    (Scope ``ai``). Bremst Provider-Kosten und CPU/RAM-Last durch einzelne
    (auch versehentlich schleifende) Nutzer, ohne Listen/Suche zu drosseln."""

    scope = "ai"
