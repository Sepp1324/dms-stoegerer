FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Systempakete: psycopg-Runtime + OCR-Werkzeuge (OCRmyPDF-Abhängigkeiten)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
        ghostscript \
        unpaper \
        pngquant \
        qpdf \
        poppler-utils \
        libzbar0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

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
