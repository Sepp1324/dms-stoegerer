# syntax=docker/dockerfile:1
#
# Multi-Stage-Build (P2): Einige Abhängigkeiten liegen NICHT als vorgebautes Wheel
# vor und müssen aus dem Source kompiliert werden – insbesondere **PyStemmer**, das
# transitiv über ``fastembed`` gezogen wird und nur als sdist existiert. Auf ARM
# (Apple-Silicon-Entwicklung, ARM-k3s-Nodes) gibt es dafür kein manylinux-Wheel, der
# reale Build scheiterte daher ohne C-Compiler. Wir kompilieren deshalb in einem
# separaten Build-Stage MIT ``build-essential`` in ein venv und kopieren nur das
# fertige venv ins schlanke Runtime-Image – der Compiler landet nie im finalen Image.

# ---------------------------------------------------------------------------
# Build-Stage: kompiliert alle (auch source-only) Python-Pakete in ein venv.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# build-essential (gcc/make/libc-dev) NUR hier – zum Kompilieren von PyStemmer &
# etwaigen weiteren sdists. Die Python-Header liefert das slim-Image selbst mit.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# In ein eigenständiges venv installieren -> lässt sich als Ganzes ins Runtime-
# Image kopieren (gleiche Basis/Python-Version, daher portabel).
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Runtime-Stage: schlankes Image OHNE Compiler, nur Laufzeit-Systempakete.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Systempakete: psycopg-Runtime, OCR-Werkzeuge und Backup-CLI
# (pg_dump/ssh/rsync werden vom k8s Backup-CronJob im selben Image genutzt).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        postgresql-client \
        openssh-client \
        rsync \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
        ghostscript \
        unpaper \
        pngquant \
        qpdf \
        poppler-utils \
        libzbar0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Fertig kompilierte Abhängigkeiten aus dem Build-Stage übernehmen (kein pip-Build,
# kein gcc im finalen Image).
COPY --from=builder /opt/venv /opt/venv

# Kein Modell-Baking mehr (Rollback auf fastembed 0.3.6): 0.3.6 lädt das e5-Modell
# klassisch nach EMBEDDING_CACHE_DIR (Default /data/models, persistentes PVC) und
# nutzt den dort bereits vorhandenen Cache – kein Xet, kein ins-Image-Backen.

COPY . .

# Static-Dateien zur Build-Zeit sammeln → landen im Image, WhiteNoise liefert
# sie aus dem Hauptcontainer aus. (Läuft ohne DB-Verbindung mit Default-Env.)
RUN python manage.py collectstatic --noinput

# Datenverzeichnis für die revisionssichere Ablage
RUN mkdir -p /data

# Version/Commit für den Health-Endpoint (spät im Build -> bricht keinen Cache
# der schweren Layer). Werden vom CI-Build als --build-arg=<Git-SHA> gesetzt.
ARG GIT_SHA=""
ARG APP_VERSION="dev"
ENV GIT_SHA=${GIT_SHA} \
    APP_VERSION=${APP_VERSION}

EXPOSE 8000

# Standard: Gunicorn. Migrationen laufen über den k8s-Init-Container bzw.
# im Compose-Command, damit der Web-Prozess selbst zustandslos startet.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
