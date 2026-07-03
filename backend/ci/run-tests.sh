#!/usr/bin/env bash
# Führt die Backend-Testsuite + Migrations-Check im gebauten Backend-Image
# gegen eine wegwerfbare Postgres-16-Instanz aus.
#
#   Aufruf:  run-tests.sh <backend-image>
#
# Warum eine echte Postgres statt SQLite: Das Backend nutzt
# django.contrib.postgres (Volltextsuche via SearchVector), das unter SQLite
# nicht läuft. Deshalb testen wir gegen dieselbe Engine wie in Produktion.
#
# Verwendet von .github/workflows/ci.yml (PR-Gate) und deploy.yml (Deploy-Gate).
# Läuft auf dem self-hosted Runner (Label "dms"), der Docker bereitstellt.
set -euo pipefail

IMAGE="${1:?Nutzung: run-tests.sh <backend-image>}"

# Eindeutige, kollisionsfreie Namen je Lauf (parallele Läufe möglich).
ID="${GITHUB_RUN_ID:-local}-$$"
NET="dms-ci-net-$ID"
PG="dms-ci-pg-$ID"

cleanup() {
  docker rm -f "$PG"    >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "::group::Wegwerf-Postgres starten"
docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_DB=dms \
  -e POSTGRES_USER=dms \
  -e POSTGRES_PASSWORD=dms \
  postgres:16 >/dev/null

echo "Warte auf Postgres…"
ready=0
for _ in $(seq 1 30); do
  if docker exec "$PG" pg_isready -U dms -q; then ready=1; break; fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  echo "Postgres wurde nicht rechtzeitig bereit." >&2
  docker logs "$PG" >&2 || true
  exit 1
fi
echo "::endgroup::"

echo "::group::Migrations-Check + Testsuite im Backend-Image"
# POSTGRES_HOST zeigt auf den DB-Container im selben Docker-Netz.
# DJANGO_SECRET_KEY/DEBUG explizit gesetzt, damit der Lauf nicht vom
# Default-Verhalten abhängt. Der User "dms" ist im Postgres-Image Superuser
# und darf damit die Test-DB (test_dms) anlegen.
docker run --rm --network "$NET" \
  -e POSTGRES_HOST="$PG" \
  -e POSTGRES_DB=dms \
  -e POSTGRES_USER=dms \
  -e POSTGRES_PASSWORD=dms \
  -e DJANGO_SECRET_KEY=ci-test-not-a-real-secret \
  -e DJANGO_DEBUG=0 \
  "$IMAGE" \
  sh -c "python manage.py makemigrations --check --dry-run && python manage.py test --noinput -v2"
echo "::endgroup::"
