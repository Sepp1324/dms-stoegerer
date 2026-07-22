"""Django-Einstellungen für das DMS.

Konfiguration erfolgt über Umgebungsvariablen (siehe .env.example).
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# .env aus dem Projekt-Root laden, falls vorhanden (lokale Entwicklung)
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR.parent / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- Kern ---
_INSECURE_DEFAULT_SECRET = "insecure-dev-key-change-me"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", _INSECURE_DEFAULT_SECRET)
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

# Fail-closed (P2): In Produktion (DEBUG=false) NICHT mit dem unsicheren
# Default-SECRET_KEY starten – sonst sind Session-Cookies und JWT-Signaturen
# trivial fälschbar. Beim Test-Runner (``manage.py test``) bewusst nachsichtig,
# damit die Suite ohne gesetztes Secret läuft.
_RUNNING_TESTS = "test" in sys.argv
if not DEBUG and not _RUNNING_TESTS and SECRET_KEY == _INSECURE_DEFAULT_SECRET:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY muss in Produktion (DJANGO_DEBUG=false) gesetzt sein "
        "– der unsichere Default ist nicht erlaubt."
    )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # Drittanbieter
    "rest_framework",
    "corsheaders",
    # Eigene Apps
    "accounts",
    "documents",
    "ai",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise liefert Static-Dateien (u. a. Django-Admin) direkt aus Gunicorn,
    # ohne separaten Webserver – wichtig fürs Cluster-Deployment (DEBUG=false).
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Datenbank ---
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "dms"),
        "USER": os.getenv("POSTGRES_USER", "dms"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "dms"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}

# Eigenes User-Modell (Rollen etc.)
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internationalisierung ---
LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

# --- Statische & Medien-Dateien ---
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Wurzel der revisionssicheren Datei-Ablage (Originale + Archiv-PDFs)
DMS_DATA_DIR = Path(os.getenv("DMS_DATA_DIR", BASE_DIR.parent / "data"))
MEDIA_ROOT = DMS_DATA_DIR

# Consume-Ordner (Eingang, z. B. vom Scanner/NAS beschickt). Der Pfad ist per
# Env übersteuerbar, damit er auf einen NFS-/NAS-Mount zeigen kann; Default
# unverändert DMS_DATA_DIR/consume (siehe storage.CONSUME_DIR). ``_processed/``
# und ``_failed/`` liegen relativ dazu.
CONSUME_FOLDER_PATH = os.getenv("CONSUME_FOLDER_PATH", "")
# NFS-Reife-Check: eine Datei erst aufnehmen, wenn seit ihrer letzten Änderung
# mindestens so viele Sekunden vergangen sind. Verhindert Teil-Reads langsam
# über NFS geschriebener Scans; zu junge Dateien holt der nächste Scan.
CONSUME_MIN_AGE = float(os.getenv("CONSUME_MIN_AGE", "15"))
# Obergrenze pro hochgeladener Datei (P0-2/DoS-Schutz). Greift in
# ``storage.save_upload``/``save_bytes`` – größere Uploads werden mit 400
# abgewiesen, bevor die Platte vollläuft. Neue Env-Var: Backend-Deployment-Env
# ggf. anpassen – KEINE Migration.
UPLOAD_MAX_FILE_MB = int(os.getenv("UPLOAD_MAX_FILE_MB", "200"))
# Pro-User-Attribution des Consume-Ingest. Ist das Flag aktiv, iteriert
# ``scan_consume_folder`` die Top-Level-Unterordner von ``CONSUME_DIR``: der
# Ordnername ist der Username, alle darin reifen Dateien werden dem passenden
# Django-User als ``Document.owner`` zugeordnet (``_processed/``/``_failed/``
# liegen pro User-Ordner). Unbekannte Ordner werden übersprungen (keine
# owner-lose Aufnahme). Default ``false`` -> unverändertes Flat-Verhalten; das
# Overlay setzt das Flag im ConfigMap-Patch auf ``true`` (nicht in der Base).
CONSUME_PER_USER = os.getenv("CONSUME_PER_USER", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# Standard-Eigentümer für Ingest ohne direkten Owner (STOAA-295). Jeweils ein
# Username; leer = bewusste Admin-Triage (owner=None). ``MAIL_DEFAULT_OWNER``
# greift, wenn ein MailAccount keinen ``owner`` gesetzt hat; ``CONSUME_DEFAULT_
# OWNER`` greift im Flat-Consume-Modus (der Per-User-Modus setzt den Owner
# ohnehin selbst). Unbekannter Username -> Warn-Log + owner=None (Triage). Neue
# Env-Vars: Backend-Image/Deployment-Env aktualisieren – KEINE Migration.
MAIL_DEFAULT_OWNER = os.getenv("MAIL_DEFAULT_OWNER", "")
CONSUME_DEFAULT_OWNER = os.getenv("CONSUME_DEFAULT_OWNER", "")
MEDIA_URL = "media/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Backup-Monitoring: wenn der letzte erfolgreiche Backup-Lauf älter ist, zeigt
# die UI/Admin-API eine Warnung. Für täglichen CronJob sind 36h bewusst großzügig:
# ein einzelner später/ausgefallener Lauf wird sichtbar, ohne sofort nachts zu lärmen.
BACKUP_ALERT_AFTER_HOURS = float(os.getenv("BACKUP_ALERT_AFTER_HOURS", "36"))
OCR_ALERT_SUCCESS_RATE = float(os.getenv("OCR_ALERT_SUCCESS_RATE", "95"))
PROCESSING_STUCK_AFTER_MINUTES = float(os.getenv("PROCESSING_STUCK_AFTER_MINUTES", "30"))
# Kurzes Caching des Qualitätscenters (#6): Das Center scored bei jedem Aufruf
# alle sichtbaren Dokumente in Python. Das Ergebnis muss nur Sekunden frisch
# sein; 0 schaltet das Caching ab.
QUALITY_STATUS_CACHE_TTL = int(os.getenv("QUALITY_STATUS_CACHE_TTL", "60"))

# --- Cache ---
# DRF-Throttling (siehe unten) speichert seine Zähler im Django-Cache. Ohne
# Konfiguration ist das der prozesslokale LocMemCache – funktioniert überall
# (auch in CI/Tests ohne Redis), zählt aber pro Gunicorn-Worker/Pod getrennt.
# In Produktion sollte ``CACHE_URL`` (Redis) gesetzt sein, damit die Limits über
# alle Worker/Pods hinweg GEMEINSAM gelten. Django 4+ bringt das Redis-Backend
# ohne Zusatzpaket mit (nutzt das bereits vorhandene ``redis``).
CACHE_URL = os.getenv("CACHE_URL", "")
if CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CACHE_URL,
            "KEY_PREFIX": "dms",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }

# --- DRF ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    # DoS-Schutz (P1): KEIN globaler Default-Throttle – die Drossel wird gezielt
    # per ``throttle_classes`` an die Upload-Pfade gehängt (documents/throttling.py),
    # damit Suche/Listen/Polling unangetastet bleiben. Die Raten sind per Env
    # tunebar (Familien-Bulk-Upload im Blick).
    "DEFAULT_THROTTLE_RATES": {
        "upload": os.getenv("THROTTLE_UPLOAD_RATE", "120/minute"),
        "capture": os.getenv("THROTTLE_CAPTURE_RATE", "60/minute"),
    },
}

# Größenobergrenze für Nicht-Datei-Formfelder eines Requests (DoS gegen riesige
# Metadaten-Payloads). Datei-Uploads werden separat in ``storage.save_upload``
# per ``UPLOAD_MAX_FILE_MB`` begrenzt. Default 10 MiB.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_MB", "10")) * 1024 * 1024

# --- CORS (React-Dev-Server spricht die API) ---
CORS_ALLOWED_ORIGINS = env_list("DJANGO_CORS_ORIGINS", "http://localhost:5173")
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

# --- Sicherheit / HTTPS-Härtung (P2) ---
# Immer aktiv (unschädlich auch lokal):
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# Die folgende Härtung greift NUR in Produktion (DEBUG=false); die lokale
# Entwicklung über http bleibt unberührt. Alle Schalter sind per Env übersteuerbar.
if not DEBUG:
    # TLS wird am Ingress (Traefik) terminiert; der Backend-Pod sieht http mit
    # X-Forwarded-Proto=https. Ohne diesen Header hielte Django jede Anfrage für
    # unverschlüsselt (und würde secure-Cookies/Redirect falsch bewerten).
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
    CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", True)
    # Django-seitiger http->https-Redirect: Default AUS, weil Traefik das i. d. R.
    # schon am Edge erledigt; per Env aktivierbar. (k8s-HTTP-Probes werten 3xx als
    # Erfolg, ein Redirect bräche sie also nicht.)
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
    # HSTS: erzwingt https im Browser nach dem ersten Besuch. includeSubDomains
    # bewusst Default AUS – der DMS teilt sich die Parent-Domain mit anderen
    # Diensten (z. B. der Registry), die HSTS nicht ungefragt erben sollen.
    # preload ebenfalls AUS (irreversible Selbstverpflichtung, Listen-Eintrag).
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)

# --- Celery ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _with_redis_auth(url: str) -> str:
    """Webt ``REDIS_PASSWORD`` in eine passwortlose redis-URL ein (P2, Redis-Auth).

    So bleibt das Passwort an EINER Stelle (Secret ``REDIS_PASSWORD``); die
    passwortlose ``REDIS_URL`` (ConfigMap) muss nicht dupliziert werden. Ist die
    URL bereits mit Zugangsdaten versehen (``@``) oder kein Passwort gesetzt,
    bleibt sie unverändert.
    """
    password = os.getenv("REDIS_PASSWORD", "")
    if not password or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:  # bereits Zugangsdaten enthalten
        return url
    # Passwort MUSS URL-kodiert werden: Sonderzeichen (z. B. aus base64: ``+/=``
    # oder ein ``:``) würden sonst die URL zerlegen und Celery/kombu mit
    # "Port could not be cast to integer" abstürzen lassen. ``safe=""`` kodiert
    # auch ``/`` und ``:``.
    from urllib.parse import quote

    return f"{scheme}://:{quote(password, safe='')}@{rest}"


REDIS_URL = _with_redis_auth(REDIS_URL)
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_TRACK_STARTED = True
CELERY_TIMEZONE = TIME_ZONE

# acks_late/reject_on_worker_lost BEWUSST NICHT global (zurückgenommen):
# * process_document_version ist (noch) NICHT wiederaufnahmefähig – es startet
#   immer bei Schritt 0. Ein nach Worker-Crash wiederzugestellter Task auf
#   Zustand HASHED/OCR_RUNNING erzeugt einen ungültigen Übergang -> die Version
#   würde fälschlich FAILED (und ein noch laufender Originaltask gestört).
# * Viele Tasks sind NICHT idempotent (push_document_flashcards sendet an
#   psychosr, KI-Aufrufe, E-Mail-Versand) – eine Wiederholung sendet doppelt.
# Sicheres Wieder-Aktivieren erfordert (a) pro-Task-Scoping auf echt idempotente
# Tasks und (b) einen wiederaufnahmefähigen process_document_version mit
# atomarem Task-Claim (Lease/Run-ID). Bis dahin bleibt es aus (Task geht bei
# Crash verloren -> Version bleibt in einem Zwischenzustand, per Retry holbar).

# Fairness/Speicher: jeder Worker zieht nur EINEN Task vorab (kein Horten), sonst
# blockiert ein langer OCR-Task die vorgezogenen und der COW-Speicher steigt.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# Ein einzelner Task darf nicht ewig hängen (z. B. OCR an einem kaputten PDF):
# Soft-Limit wirft SoftTimeLimitExceeded (Task bricht sauber ab -> FAILED/retry),
# Hard-Limit killt hart. Großzügig für große Scans, per Env justierbar. WICHTIG:
# breite ``except Exception`` in Loop-Tasks müssen SoftTimeLimitExceeded
# durchlassen (sonst läuft der Task bis zum Hard-Limit weiter).
CELERY_TASK_SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "1800"))
CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "2100"))
# OCR-Subprozess-Timeout (ocrmypdf/pdftotext) – BEWUSST unter dem Celery-Soft-
# Limit (1800 s): ein hängender OCR-Prozess inkl. tesseract-Kinder wird per
# Prozessgruppen-Kill hart beendet, bevor der Task selbst ins Limit läuft (sonst
# könnten Kindprozesse nach einem Worker-Kill weiterlaufen und Ressourcen belegen).
OCR_SUBPROCESS_TIMEOUT = int(os.getenv("OCR_SUBPROCESS_TIMEOUT", "1200"))
# OCR-Qualität: Mindestanteil der Seiten mit ausreichend Text, damit eine
# OCR-Ausgabe als Archiv veröffentlicht wird (Pro-Seite-Deckung statt nur
# Durchschnitt). 0.6 = mind. 60% der Seiten müssen Text tragen; einzelne bewusst
# leere Seiten (z. B. Rückseiten) bleiben tolerierbar.
OCR_MIN_PAGE_COVERAGE = float(os.getenv("OCR_MIN_PAGE_COVERAGE", "0.6"))

# Periodische Aufgaben (benötigt einen laufenden ``celery beat``-Prozess, siehe
# deploy/k8s/beat.yaml). Intervalle in Sekunden, per Env übersteuerbar.
CELERY_BEAT_SCHEDULE = {
    "fetch-mail-accounts": {
        "task": "documents.tasks.fetch_all_mail_accounts",
        "schedule": float(os.getenv("MAIL_FETCH_INTERVAL", "300")),
    },
    "scan-consume-folder": {
        "task": "documents.tasks.scan_consume_folder",
        "schedule": float(os.getenv("CONSUME_SCAN_INTERVAL", "120")),
    },
    # Wiedervorlagen/Erinnerungen (STOAA-372): einmal täglich prüfen, ob eine
    # Erinnerung fällig ist, und ``notified_at`` genau einmal setzen.
    "check-due-reminders": {
        "task": "documents.tasks.check_due_reminders",
        "schedule": float(os.getenv("REMINDER_CHECK_INTERVAL", "86400")),
    },
    # Stuck-Task-Watchdog: hängende Versionen (Worker-Crash, acks_late ist aus)
    # nach PROCESSING_STUCK_AFTER_MINUTES automatisch FAILED (retry-fähig) bzw.
    # hängendes SEALED nach READY abschließen.
    "reap-stuck-versions": {
        "task": "documents.tasks.reap_stuck_versions",
        "schedule": float(os.getenv("STUCK_REAP_INTERVAL", "600")),
    },
    # psychosr-Sync-Watchdog: hängende/offene FlashcardSyncEntry (Worker-Crash,
    # acks_late ist aus) periodisch neu einplanen, damit offene Karten auch ohne
    # erneutes Taggen eventuell zugestellt werden (endgültig FAILED bleibt liegen).
    "reap-stuck-flashcard-syncs": {
        "task": "documents.tasks.reap_stuck_flashcard_syncs",
        "schedule": float(os.getenv("FLASHCARD_REAP_INTERVAL", "900")),
    },
}

# psychosr-Kartensync: nach wie vielen fehlgeschlagenen Push-Versuchen eine Karte
# endgültig FAILED wird (kein Endlos-Retry; Monitoring über last_error). Und das
# Stale-Fenster, ab dem ein hängendes ``in_progress`` neu geclaimt werden darf.
PSYCHOSR_MAX_CARD_ATTEMPTS = int(os.getenv("PSYCHOSR_MAX_CARD_ATTEMPTS", "10"))
PSYCHOSR_CLAIM_STALE_MINUTES = int(os.getenv("PSYCHOSR_CLAIM_STALE_MINUTES", "15"))

# --- E-Mail-Versand (SMTP) ---
# Erinnerungs-Mails (STOAA-372) werden NUR verschickt, wenn ein SMTP-Host
# konfiguriert ist. Ohne ``EMAIL_HOST`` überspringt ``check_due_reminders`` den
# Versand still (kein Fehler); die In-App-Benachrichtigung (die due-Liste)
# funktioniert unabhängig davon. Default leer = SMTP nicht konfiguriert
# (überschreibt Djangos Standard ``localhost``, damit der Leer-Fall eindeutig
# als „nicht konfiguriert" erkennbar ist).
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "dms@localhost")

# --- AI-Anbindung ---
AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic")
# Default: leistungsfähigstes Modell. Für Massen-Klassifizierung ist
# claude-haiku-4-5 deutlich günstiger – per AI_MODEL umschaltbar.
AI_MODEL = os.getenv("AI_MODEL", "claude-opus-4-8")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# --- psychosr-Anbindung (Auto-Lernkarten) ---
# Sobald ein Dokument den Trigger-Tag erhält, erzeugt das DMS daraus MC-Fragen
# und pusht sie an den SR-Trainer psychosr (POST /api/mc/add, Header X-Token).
# Ohne PSYCHOSR_URL + PSYCHOSR_TOKEN ist die Automatik inaktiv (keine Wirkung).
PSYCHOSR_URL = os.getenv("PSYCHOSR_URL", "")
PSYCHOSR_TOKEN = os.getenv("PSYCHOSR_TOKEN", "")
PSYCHOSR_TRIGGER_TAG = os.getenv("PSYCHOSR_TRIGGER_TAG", "Psychologie")
PSYCHOSR_SYNCED_TAG = os.getenv("PSYCHOSR_SYNCED_TAG", "psychosr-synced")
PSYCHOSR_DECK = os.getenv("PSYCHOSR_DECK", "mc")
PSYCHOSR_MAX_QUESTIONS = int(os.getenv("PSYCHOSR_MAX_QUESTIONS", "8"))

# --- Semantische Suche: lokale Text-Embeddings (fastembed/ONNX) ---
# EMBEDDING_MODEL muss zu EMBEDDING_DIM passen. Default: mehrsprachiges e5-large
# (1024-dim, gut für Deutsch). Modell-Cache auf dem persistenten /data-PVC, damit
# es nicht bei jedem Worker-Neustart neu geladen wird.
EMBEDDING_ENABLED = os.getenv("EMBEDDING_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# In der Testsuite standardmaessig AUS: die Verarbeitungspipeline ruft den
# semantischen Index synchron auf; ohne diesen Schalter wuerde jeder Pipeline-Test
# das ~1 GB grosse fastembed-Modell laden. Dedizierte Embedding-Tests patchen
# ``ai.embeddings.enabled``/``embed_*`` und umgehen diesen Default gezielt.
if "test" in sys.argv:
    EMBEDDING_ENABLED = False
# Default jetzt das kleine mehrsprachige MiniLM (384-dim): e5-large (1024) lud
# selbst mit 8Gi nicht (onnxruntime-Graph-Optimierungs-Spike, OOMKill). MiniLM
# (~470 MB) lädt problemlos. MUSS zu EMBEDDING_DIM (384) + models.EMBEDDING_DIM
# passen. Modell-Cache auf dem persistenten /data-PVC.
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))
EMBEDDING_CACHE_DIR = os.getenv("EMBEDDING_CACHE_DIR", str(DMS_DATA_DIR / "models"))
# Modell-spezifische Prefixe. e5 verlangt "passage: "/"query: "; MiniLM/andere
# NICHT (sonst würde das Wort "passage" mit-eingebettet). Default leer (MiniLM);
# für e5 per Env auf "passage: "/"query: " setzen.
EMBEDDING_PASSAGE_PREFIX = os.getenv("EMBEDDING_PASSAGE_PREFIX", "")
EMBEDDING_QUERY_PREFIX = os.getenv("EMBEDDING_QUERY_PREFIX", "")
# onnxruntime-Intra-Op-Threads für das Embedding. Default 2 statt „so viele wie
# Node-CPUs": jeder Thread reserviert Speicher-Arenen -> ungedeckelt sprengte das
# Laden/Embedden von e5-large das Pod-Memory-Limit (OOMKill/exit 137). Passt zum
# CPU-Limit "2". 0 = fastembed-Default (nicht deckeln).
EMBEDDING_THREADS = int(os.getenv("EMBEDDING_THREADS", "2"))
# Mindest-Cosine-Aehnlichkeit (0..1) fuer semantische Treffer. e5-Embeddings sind
# normalisiert und liegen fuer relevante Paare hoch/eng beieinander; der Floor
# schneidet Rauschen ab, ist aber bewusst konservativ (lieber Recall als leere
# Ergebnisse) und ueber die Env feinjustierbar, sobald echte Daten vorliegen.
EMBEDDING_MIN_SIMILARITY = float(os.getenv("EMBEDDING_MIN_SIMILARITY", "0.70"))

# --- Auto-Ablage / Autopilot (kNN über Embeddings) ---
# AUTO_FILE_ENABLED steuert NUR das automatische Einsortieren beim Ingest (Opt-in,
# Default aus – der Nutzer soll das bewusst aktivieren). Die manuelle Batch-Aktion
# „Posteingang aufräumen" und der Vorschlag in der Detailansicht laufen unabhängig
# davon. AUTO_FILE_MIN_CONFIDENCE ist die Schwelle, ab der ein Vorschlag ohne
# Rückfrage übernommen wird (bewusst hoch, damit der Autopilot nur bei klarer
# Faktenlage zugreift).
AUTO_FILE_ENABLED = os.getenv("AUTO_FILE_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
AUTO_FILE_MIN_CONFIDENCE = float(os.getenv("AUTO_FILE_MIN_CONFIDENCE", "0.75"))

# --- Dubletten-/Versionserkennung (Cosine-Ähnlichkeit der Embeddings) ---
# THRESHOLD: ab hier gilt ein Dokument als „mögliche Version" (sehr ähnlich).
# STRONG: ab hier praktisch dasselbe Dokument („Duplikat"). Über Env justierbar,
# sobald echte Daten zeigen, wie eng Re-Scans desselben Belegs beieinanderliegen.
DUPLICATE_THRESHOLD = float(os.getenv("DUPLICATE_THRESHOLD", "0.93"))
DUPLICATE_STRONG_THRESHOLD = float(os.getenv("DUPLICATE_STRONG_THRESHOLD", "0.97"))
# Lexikalisches Zusatzsignal (Jaccard über OCR-Tokens): trennt echte Doppel-Scans
# (nahezu identischer Text) von wiederkehrenden, aber verschiedenen Dokumenten mit
# gleicher Vorlage (z. B. monatliche Rechnungen), die semantisch fast gleich sind.
# Nur wenn Cosine >= STRONG UND lexikalisch >= diesem Wert gilt "Duplikat".
DUPLICATE_LEXICAL_STRONG = float(os.getenv("DUPLICATE_LEXICAL_STRONG", "0.80"))

# --- ASN-Barcode-Erkennung (STOAA-515) ---
# pyzbar + libzbar0 müssen installiert sein; fehlen sie → WARN + Fallback auf OCR-Text.
ASN_BARCODE_ENABLED = os.getenv("ASN_BARCODE_ENABLED", "true").lower() in ("1", "true", "yes")
ASN_BARCODE_PREFIX = os.getenv("ASN_BARCODE_PREFIX", "ASN")
ASN_BARCODE_DPI = int(os.getenv("ASN_BARCODE_DPI", "300"))
# Komma-getrennte Seitenzahlen (1-basiert) oder leer = alle Seiten scannen.
ASN_BARCODE_PAGES = os.getenv("ASN_BARCODE_PAGES", "")
