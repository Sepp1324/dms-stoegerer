"""Django-Einstellungen für das DMS.

Konfiguration erfolgt über Umgebungsvariablen (siehe .env.example).
"""
import os
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
MEDIA_URL = "media/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

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
}

# --- AI-Anbindung ---
AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic")
# Default: leistungsfähigstes Modell. Für Massen-Klassifizierung ist
# claude-haiku-4-5 deutlich günstiger – per AI_MODEL umschaltbar.
AI_MODEL = os.getenv("AI_MODEL", "claude-opus-4-8")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
