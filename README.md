# DMS

Eigenes Dokumenten-Management-System, das die Stärken von **paperless-ngx**
(offen, moderne UI, OCR/ML) und **ecoDMS** (Versionierung, feingranulare Rechte,
regelbasierte Klassifizierung, revisionssichere Ablage) vereint.

Konzept & Architektur: siehe [KONZEPT.md](KONZEPT.md).

## Stack

- **Backend:** Django 5 + Django REST Framework
- **Task-Queue:** Celery + Redis (OCR, Klassifizierung, E-Mail-Abruf – ab Stufe 1)
- **Datenbank:** PostgreSQL 16
- **Frontend:** React + Vite + TypeScript (SPA)
- **KI:** Provider-Abstraktion (Claude / Ollama / OpenAI, umschaltbar)
- **Deployment:** Docker Compose (lokal) · Kubernetes-Manifeste für **k3s**

## Projektstruktur

```
backend/          Django-Projekt
  config/         Settings, URLs, Celery
  accounts/       Nutzer + Rollen (Admin/Nutzer/Gast)
  documents/      Kern-Datenmodell (Document, DocumentVersion mit Hash-Kette, …)
  ai/             KI-Anbindung (Provider-Abstraktion + Services)
frontend/         React/Vite-SPA
deploy/
  docker-compose.yml   lokale Entwicklung
  k8s/                 k3s-Manifeste (kustomize)
```

## Stufe 0 – was schon steht

- Lauffähiges Gerüst: Django + Postgres + Redis + Celery + Frontend
- Datenmodell inkl. Migrationen und nutzbarem Django-Admin
- Auth (JWT + Session) und Rollen
- Health-Check-Endpoint `/api/health/`, den die SPA anzeigt
- KI-Modul als Provider-Abstraktion (noch nicht in die Pipeline eingehängt)

Noch **nicht** enthalten (folgt laut Roadmap in KONZEPT.md): OCR-Pipeline,
Volltextsuche, Klassifizierung, E-Mail-Ingestion, Revisionssicherheit.

## Lokal starten (Docker Compose)

```bash
cp .env.example .env          # Werte anpassen (mind. DJANGO_SECRET_KEY)
docker compose -f deploy/docker-compose.yml up --build
```

- Backend/API: http://localhost:8000/api/health/
- Admin:       http://localhost:8000/admin/
- Frontend:    http://localhost:8080

Admin-Nutzer anlegen:

```bash
docker compose -f deploy/docker-compose.yml exec backend \
  python manage.py createsuperuser
```

### Frontend-Entwicklung mit Hot-Reload

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxyt /api an :8000
```

## Deployment auf k3s

```bash
# 1. Images bauen und in k3s importieren (containerd)
docker build -t dms-backend:latest ./backend
docker build -t dms-frontend:latest ./frontend
docker save dms-backend:latest | sudo k3s ctr images import -
docker save dms-frontend:latest | sudo k3s ctr images import -

# 2. Secret vorbereiten
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml
# Werte in secret.yaml eintragen
kubectl apply -f deploy/k8s/secret.yaml

# 3. Alles ausrollen
kubectl apply -k deploy/k8s

# 4. dms.local in /etc/hosts auf die Node-IP zeigen lassen
```

Danach ist das DMS unter `http://dms.local` erreichbar (Traefik-Ingress).

## Nächste Schritte

Stufe 1 (MVP „paperless-Kern"): Upload + Consume-Ordner → OCR-Pipeline
(OCRmyPDF/Tesseract) → durchsuchbares PDF/A, Volltextsuche und
Dokument-Ansicht in der SPA. Details in [KONZEPT.md](KONZEPT.md) §7.
