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
import threading
import time

from rest_framework.throttling import SimpleRateThrottle

logger = logging.getLogger(__name__)

# Prozess-lokaler Fallback-Zähler (P2): Fällt der geteilte Redis-Cache aus, war das
# Throttle bisher vollständig offen (fail-open) – Login/Refresh/Upload/KI-Limits
# griffen dann GAR NICHT mehr. Der Kommentar verwies auf ein vorgeschaltetes
# Traefik-Rate-Limit, das im Deployment aber nicht existiert. Statt komplett offen
# zu sein, zählt dieser In-Memory-Fallback pro Prozess weiter. Er gilt NICHT über
# alle Pods gemeinsam (darum nur Fallback, nicht der Normalpfad), begrenzt aber
# einen Brute-Force gegen einen einzelnen Pod deutlich. Die Historie ist je Key
# eine absteigend nach Zeit sortierte Liste von Zeitstempeln (analog DRF).
_FALLBACK_LOCK = threading.Lock()
_FALLBACK_HISTORY: dict[str, list[float]] = {}
# Notbremse gegen unbegrenztes Wachstum bei einem langen Ausfall mit sehr vielen
# unterschiedlichen IPs: übersteigt der Store diese Größe, wird er verworfen
# (die Zähler starten neu – akzeptable Degradation während eines Cache-Ausfalls).
_FALLBACK_MAX_KEYS = 50_000


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
        """Bei Cache-Ausfall (P2) auf einen prozess-lokalen Zähler zurückfallen statt
        vollständig offen zu sein. Der Zähler liegt normal in Redis; ist Redis nicht
        erreichbar, warf ``super().allow_request`` einen ungefangenen
        ``ConnectionError`` -> HTTP 500 (das legte u. a. den gedrosselten LOGIN lahm).
        Ein Cache-Ausfall darf den Request NICHT mit 500 killen – aber auch nicht
        jedes Limit komplett aushebeln. Deshalb greift der In-Memory-Fallback
        (per-Pod)."""
        try:
            return super().allow_request(request, view)
        except Exception:  # noqa: BLE001 – Redis/Cache weg: lokaler Fallback statt 500
            logger.warning(
                "Throttle-Cache (%s) nicht erreichbar – prozess-lokaler "
                "Fallback-Zähler greift.", getattr(self, "scope", "?"),
            )
            return self._local_fallback_allow(request, view)

    def _local_fallback_allow(self, request, view) -> bool:
        """Sliding-Window im Prozessspeicher, mit derselben Rate wie im Normalpfad."""
        rate = getattr(self, "rate", None)
        num_requests = getattr(self, "num_requests", None)
        duration = getattr(self, "duration", None)
        if not rate or not num_requests or not duration:
            return True  # keine Rate konfiguriert -> nicht drosseln
        try:
            key = self.get_cache_key(request, view)
        except Exception:  # noqa: BLE001 – Key nicht bestimmbar -> durchlassen
            return True
        if key is None:
            return True
        now = time.time()
        cutoff = now - duration
        with _FALLBACK_LOCK:
            if len(_FALLBACK_HISTORY) > _FALLBACK_MAX_KEYS:
                _FALLBACK_HISTORY.clear()
            history = _FALLBACK_HISTORY.get(key, [])
            # Einträge außerhalb des Fensters am Listenende verwerfen.
            while history and history[-1] <= cutoff:
                history.pop()
            allowed = len(history) < num_requests
            if allowed:
                history.insert(0, now)
            _FALLBACK_HISTORY[key] = history
        # DRF ruft bei einem gedrosselten Request (allow_request -> False)
        # anschließend ``wait()`` für den Retry-After-Header auf und greift dabei
        # auf ``self.history`` und ``self.now`` zu. Im Fehlerpfad hatte
        # ``super().allow_request`` diese Attribute NICHT gesetzt (der Cache-Zugriff
        # warf davor) -> AttributeError -> HTTP 500 statt 429. Deshalb hier die von
        # ``wait()`` erwarteten DRF-Attribute setzen (Historie ist wie bei DRF
        # absteigend nach Zeit; num_requests/duration stehen aus __init__ bereit).
        self.key = key
        self.now = now
        self.history = history
        return allowed


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


class RuleSimulateRateThrottle(_PerUserScopeThrottle):
    """Limit für die Regel-Simulation (Scope ``rule_simulate``, P2).

    Jede Simulation zählt + durchläuft den GESAMTEN sichtbaren Dokumentbestand
    synchron. Ohne Drossel könnten wiederholte Aufrufe die Webworker binden."""

    scope = "rule_simulate"
