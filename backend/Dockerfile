FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

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

COPY requirements.txt .
RUN pip install -r requirements.txt

# Embedding-Modell zur BUILD-Zeit ins Image backen (kein Laufzeit-Download im
# Cluster – der schlug via hf-xet fehl). EMBEDDING_CACHE_DIR zeigt auf einen
# NICHT gemounteten Pfad (/opt/models), damit der /data-PVC ihn zur Laufzeit
# nicht überdeckt. BAKE_MODEL=0 überspringt den ~1-GB-Download – so bleibt die
# PR-CI (gemockte Embeddings) schnell; nur der Deploy-Build backt tatsächlich.
ARG BAKE_MODEL=1
ENV EMBEDDING_CACHE_DIR=/opt/models
COPY ci/bake_model.py /tmp/bake_model.py
RUN if [ "$BAKE_MODEL" = "1" ]; then python /tmp/bake_model.py; fi

COPY . .

# Static-Dateien zur Build-Zeit sammeln → landen im Image, WhiteNoise liefert
# sie aus dem Hauptcontainer aus. (Läuft ohne DB-Verbindung mit Default-Env.)
RUN python manage.py collectstatic --noinput

# Datenverzeichnis für die revisionssichere Ablage
RUN mkdir -p /data

EXPOSE 8000

# Standard: Gunicorn. Migrationen laufen über den k8s-Init-Container bzw.
# im Compose-Command, damit der Web-Prozess selbst zustandslos startet.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
