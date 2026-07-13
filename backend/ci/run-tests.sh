#!/usr/bin/env bash
# Führt Django-Check + Migrations-Check + Backend-Testsuite im gebauten
# Backend-Image gegen eine wegwerfbare Postgres-16-Instanz aus.
#
#   Aufruf:  run-tests.sh <backend-image>
#
# Warum eine echte Postgres statt SQLite: Das Backend nutzt
# django.contrib.postgres (Volltextsuche via SearchVector), das unter SQLite
# nicht läuft. Deshalb testen wir gegen dieselbe Engine wie in Produktion.
#
# Verwendet von .github/workflows/pr-checks.yml (PR-Gate) und deploy.yml
# (Deploy-Gate).
# Läuft auf dem self-hosted Runner (Label "dms"), der Docker bereitstellt.
set -euo pipefail

IMAGE="${1:?Nutzung: run-tests.sh <backend-image>}"
# pgvector-Image, damit die vector-Extension (semantische Suche) in den Tests
# per Migration angelegt werden kann. Datenformat = postgres:16.
POSTGRES_IMAGE="${CI_POSTGRES_IMAGE:-pgvector/pgvector:pg16}"
POSTGRES_TMPFS_SIZE="${CI_POSTGRES_TMPFS_SIZE:-1024m}"
MIN_FREE_KB="${CI_MIN_FREE_KB:-1048576}"

# Eindeutige, kollisionsfreie Namen je Lauf (parallele Läufe möglich).
ID="${GITHUB_RUN_ID:-local}-$$"
NET="dms-ci-net-$ID"
PG="dms-ci-pg-$ID"

cleanup() {
  docker rm -f "$PG"    >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

print_storage_diagnostics() {
  echo "--- df -h ---"
  df -h . /var/lib/docker 2>/dev/null || df -h
  echo "--- docker system df ---"
  docker system df || true
}

prune_ci_leftovers() {
  # Der self-hosted Runner ist klein und langlebig. Alte CI-Container, ungenutzte
  # Images (v. a. die je Lauf getaggten dms-backend:<sha>) und BuildKit-Cache sind
  # die häufigste Ursache für „Platte voll" / kaputte Postgres-Init-Läufe.
  docker ps -aq --filter "name=dms-ci-pg-" | xargs -r docker rm -f >/dev/null 2>&1 || true
  docker network ls -q --filter "name=dms-ci-net-" | xargs -r docker network rm >/dev/null 2>&1 || true
  docker container prune -f --filter "label=at.stoegerer.dms.ci=true" >/dev/null 2>&1 || true
  # Ungenutzte Images abräumen: dangling immer, getaggte ab 48h Alter (räumt die
  # aufgelaufenen alten CI-SHA-Images weg, schont aber frische Basis-Images →
  # keine unnötigen Re-Pulls im Normalfall).
  docker image prune -f >/dev/null 2>&1 || true
  docker image prune -af --filter "until=48h" >/dev/null 2>&1 || true
  docker builder prune -f --filter "until=24h" >/dev/null 2>&1 || true
}

reclaim_hard() {
  # Letzter Ausweg, wenn der sanfte Prune nicht reicht: ALLES Ungenutzte weg
  # (auch frische Images/Cache). Kostet nur Re-Pull/Rebuild, kein Datenverlust –
  # der Runner hält keine bleibenden Daten.
  echo "Speicher weiterhin knapp – räume aggressiv nach (alle ungenutzten Images + Build-Cache)…" >&2
  docker image prune -af >/dev/null 2>&1 || true
  docker builder prune -af >/dev/null 2>&1 || true
}

free_kb_of() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {print $4}'; }

space_low() {
  local path free_kb
  for path in "." "/var/lib/docker"; do
    [ -e "$path" ] || continue
    free_kb="$(free_kb_of "$path")"
    [ -n "$free_kb" ] || continue
    [ "$free_kb" -lt "$MIN_FREE_KB" ] && return 0
  done
  return 1
}

assert_free_space() {
  local path="$1"
  [ -e "$path" ] || return 0

  local free_kb
  free_kb="$(free_kb_of "$path" || true)"
  [ -n "$free_kb" ] || return 0
  if [ "${free_kb:-0}" -lt "$MIN_FREE_KB" ]; then
    echo "Zu wenig freier Speicher auf $path: ${free_kb:-0} KiB frei, benötigt mindestens $MIN_FREE_KB KiB." >&2
    print_storage_diagnostics >&2
    exit 1
  fi
}

echo "::group::CI-Docker-Speicher prüfen"
prune_ci_leftovers
# Reicht der sanfte Prune nicht, einmal aggressiv nachräumen, bevor wir aufgeben.
if space_low; then
  reclaim_hard
fi
print_storage_diagnostics
assert_free_space "."
assert_free_space "/var/lib/docker"
echo "::endgroup::"

echo "::group::Wegwerf-Postgres starten"
docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" \
  --label at.stoegerer.dms.ci=true \
  --tmpfs "/var/lib/postgresql/data:rw,size=$POSTGRES_TMPFS_SIZE" \
  -e POSTGRES_DB=dms \
  -e POSTGRES_USER=dms \
  -e POSTGRES_PASSWORD=dms \
  "$POSTGRES_IMAGE" >/dev/null

echo "Warte auf Postgres…"
ready=0
for _ in $(seq 1 30); do
  if [ "$(docker inspect -f '{{.State.Running}}' "$PG" 2>/dev/null || echo false)" != "true" ]; then
    echo "Postgres-Container ist vor dem Ready-Check beendet." >&2
    docker logs "$PG" >&2 || true
    print_storage_diagnostics >&2
    exit 1
  fi
  if docker exec "$PG" pg_isready -U dms -q; then ready=1; break; fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  echo "Postgres wurde nicht rechtzeitig bereit." >&2
  docker logs "$PG" >&2 || true
  exit 1
fi
echo "::endgroup::"

echo "::group::Django-Check + Migrations-Check + Testsuite im Backend-Image"
# POSTGRES_HOST zeigt auf den DB-Container im selben Docker-Netz.
# DJANGO_SECRET_KEY/DEBUG explizit gesetzt, damit der Lauf nicht vom
# Default-Verhalten abhängt. Der User "dms" ist im Postgres-Image Superuser
# und darf damit die Test-DB (test_dms) anlegen.
#
# Reihenfolge (harter Abbruch bei jedem Fehler dank &&):
#   1. manage.py check                   – System-/Konfig-Checks
#   2. makemigrations --check --dry-run  – fail bei vergessenen Migrationen
#   3. manage.py test                    – Testsuite
docker run --rm --network "$NET" \
  -e POSTGRES_HOST="$PG" \
  -e POSTGRES_DB=dms \
  -e POSTGRES_USER=dms \
  -e POSTGRES_PASSWORD=dms \
  -e DJANGO_SECRET_KEY=ci-test-not-a-real-secret \
  -e DJANGO_DEBUG=0 \
  "$IMAGE" \
  sh -c "python manage.py check && python manage.py makemigrations --check --dry-run && python manage.py test --noinput -v2"
echo "::endgroup::"
