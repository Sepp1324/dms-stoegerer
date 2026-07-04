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

## Django-Migrationen: Konflikt-Prävention

**Problem:** Wenn mehrere Feature-Branches parallel Modell-Änderungen vornehmen,
entstehen Migration-Dateien mit derselben Nummer (z. B. mehrere `0007_*.py`).
Beim Merge führt das zu einem P0, weil Django nicht weiß, welche Migration zuerst
laufen soll.

**Lösung:** Die CI prüft mit `makemigrations --check --dry-run` (siehe
`.github/workflows/pr-checks.yml`), ob ausstehende Migrationen fehlen. Ein roter
CI-Build blockt den Merge. Zusätzlich verhindert folgender Workflow die meisten
Konflikte:

### Workflow für Migrations-PRs

1. **Vor dem Branchen:** `git switch main && git pull` – immer vom aktuellen `main` abzweigen.
2. **Migration sofort erstellen:** Nach der Modell-Änderung direkt `python manage.py makemigrations` ausführen und committen. Nicht aufschieben.
3. **Vor dem PR:** `git fetch origin main && git rebase origin/main` – wenn `main` inzwischen weitergegangen ist, neu aufsetzen. Django erkennt beim Rebase Konflikt-Nummern und `makemigrations` schlägt fehl → du siehst es lokal, bevor der PR rot wird.
4. **Bei Konflikt:** siehe unten (Migrations-Merge).

### Migrations-Konflikt auflösen

Wenn trotzdem zwei `0007_*`-Dateien kollidieren (z. B. nach parallelem Merge):

```bash
# 1. Kollidierende Migrationen identifizieren
ls -1 backend/documents/migrations/0007_*

# 2. Merge-Migration anlegen – Django fügt beide Zweige zusammen
python manage.py makemigrations --merge

# 3. Committen
git add backend/documents/migrations/
git commit -m "fix: merge parallel migrations 0007"
```

Django erzeugt eine `0008_merge_…`-Datei, die beide `0007_*` als Vorgänger
referenziert. Das löst den Konflikt, ohne Daten zu verlieren.

> **CI-Gate:** `.github/workflows/pr-checks.yml` führt `makemigrations --check`
> aus – ein fehlender Merge schlägt fehl und blockt den PR, BEVOR kaputter Code
> auf `main` landet. Details: [docs/ci-cd.md](docs/ci-cd.md).

---

## PRs sind erst mergebar, wenn `pr-checks` grün ist

Jeder Pull Request gegen `main` löst automatisch den Workflow
[`.github/workflows/pr-checks.yml`](.github/workflows/pr-checks.yml) auf dem
self-hosted Runner aus:

- **backend-tests** – baut das Backend-Image und führt darin `manage.py check`,
  `makemigrations --check --dry-run` und die Testsuite gegen eine wegwerfbare
  Postgres 16 aus (Logik in [`backend/ci/run-tests.sh`](backend/ci/run-tests.sh)).
- **frontend-build** – baut das Frontend-Image (`npm install` + `npm run build`,
  also `tsc -b && vite build`); ein TS-Fehler lässt den Build rot werden.
- **pr-checks** – aggregierendes Gate, wird nur grün, wenn beide obigen Jobs
  grün sind.

Dieser Workflow **deployt nicht** – der Deploy bleibt separat auf `push → main`
([`deploy.yml`](.github/workflows/deploy.yml)).

> **Owner-Setup (einmalig):** GitHub → **Settings → Branches** → Regel für
> `main` → „Require status checks to pass before merging“ → Status-Check
> **`pr-checks`** auswählen. Erst danach ist ein PR mit rotem Gate nicht mehr
> mergebar. Siehe [docs/ci-cd.md](docs/ci-cd.md).

---

## Nächste Schritte

Stufe 1 (MVP „paperless-Kern"): Upload + Consume-Ordner → OCR-Pipeline
(OCRmyPDF/Tesseract) → durchsuchbares PDF/A, Volltextsuche und
Dokument-Ansicht in der SPA. Details in [KONZEPT.md](KONZEPT.md) §7.
