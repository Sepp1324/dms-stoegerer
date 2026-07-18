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
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "insecure-dev-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

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
}

# --- CORS (React-Dev-Server spricht die API) ---
CORS_ALLOWED_ORIGINS = env_list("DJANGO_CORS_ORIGINS", "http://localhost:5173")
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

# --- Celery ---
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_TASK_TRACK_STARTED = True
CELERY_TIMEZONE = TIME_ZONE

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
}

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
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
EMBEDDING_CACHE_DIR = os.getenv("EMBEDDING_CACHE_DIR", str(DMS_DATA_DIR / "models"))
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
