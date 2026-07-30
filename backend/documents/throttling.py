"""Rate-Limits für die Upload-Pfade (P1, DoS-Schutz).

Feste, per-Nutzer gescopte Drosseln – bewusst als ``SimpleRateThrottle``-
Subklassen mit hartem ``scope`` statt ``ScopedRateThrottle`` (dessen Scope aus
``view.throttle_scope`` gelesen wird und bei ViewSet-Actions unzuverlässig
greift). Die Raten stehen in ``settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']``
(env-tunebar) und die Zähler liegen im Django-Cache (in Produktion Redis, damit
das Limit über alle Pods GEMEINSAM gilt – siehe ``settings.CACHE_URL``).
"""
from __future__ import annotations

import logging

from rest_framework.throttling import SimpleRateThrottle

logger = logging.getLogger(__name__)


class _PerUserScopeThrottle(SimpleRateThrottle):
    """Drossel mit festem Scope, gekeyt auf den authentifizierten Nutzer."""

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            ident = user.pk
        else:
            ident = self.get_ident(request)  # Fallback: Client-IP
        return self.cache_format % {"scope": self.scope, "ident": ident}

    def allow_request(self, request, view):
        """Fail-open bei Cache-Ausfall (P1): Der Zähler liegt in Redis. Ist Redis
        nicht erreichbar, warf ``super().allow_request`` einen ungefangenen
        ``ConnectionError`` -> HTTP 500. Insbesondere legte das den (jetzt
        gedrosselten) LOGIN komplett lahm. Ein Cache-Ausfall darf den Request NICHT
        mit 500 killen; das Throttle lässt ihn dann durch (die eigentliche Auth-/
        Berechtigungsprüfung bleibt unberührt). Das äußere Traefik-Rate-Limit dient
        als Fallback-Schranke."""
        try:
            return super().allow_request(request, view)
        except Exception:  # noqa: BLE001 – Redis/Cache weg: fail-open statt 500
            logger.warning(
                "Throttle-Cache (%s) nicht erreichbar – Request wird durchgelassen "
                "(fail-open).", getattr(self, "scope", "?"),
            )
            return True


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


class PdfThumbnailRateThrottle(_PerUserScopeThrottle):
    """Limit für den PDF-Werkbank-Thumbnail-Endpunkt (Scope ``pdf_thumbnail``).

    Jede Miniatur rendert serverseitig mit Poppler. Der browserseitige
    Concurrency-Gate schützt nur EINEN Tab; mehrere Tabs/Nutzer/direkte
    API-Aufrufe umgehen ihn. Diese Drossel greift serverseitig (P1)."""

    scope = "pdf_thumbnail"


class RevisionExportRateThrottle(_PerUserScopeThrottle):
    """Limit für den Revisionspaket-Export (Scope ``revision_export``).

    Der Export baut SYNCHRON ein ZIP über alle Versionen/Archive eines Dokuments
    und kann bei nur zwei Gunicorn-Workern beide binden. Diese Drossel begrenzt
    die Frequenz pro Nutzer (P2)."""

    scope = "revision_export"


class LoginRateThrottle(_PerUserScopeThrottle):
    """Limit für den JWT-Login (Scope ``login``, P1).

    Der Login ist unauthentifiziert -> die Basisklasse keyt auf die Client-IP.
    Bremst Brute-Force gegen die öffentliche Instanz. Ergänzend empfohlen: ein
    Traefik-Rate-Limit vor der App und optional django-axes (Account-Lockout)."""

    scope = "login"


class TokenRefreshRateThrottle(_PerUserScopeThrottle):
    """Limit für den JWT-Refresh (Scope ``token_refresh``, P1).

    Verhindert das Durchprobieren gestohlener/geratener Refresh-Tokens. Keyt für
    den (unauthentifizierten) Endpunkt auf die Client-IP."""

    scope = "token_refresh"


class IntegrityCheckRateThrottle(_PerUserScopeThrottle):
    """Limit für Integritäts-/Evidence-Endpunkte (Scope ``integrity_check``).

    ``integrity``, ``evidence`` und ``evidence_status`` lesen und HASHEN synchron
    sämtliche betroffenen Dateien. Ohne Drossel könnten wenige große Requests bei
    nur zwei Gunicorn-Workern das Backend blockieren (analog Revisionspaket-Export,
    P2)."""

    scope = "integrity_check"
