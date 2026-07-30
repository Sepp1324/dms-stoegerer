#!/usr/bin/env bash
# Deploy-Preflight: dms-db-backup-secrets muss existieren, BEVOR der Backup-CronJob
# ausgerollt wird (P2).
#
# Der aktive backup-CronJob referenziert das Secret per ``secretKeyRef``. Fehlt es
# (oder fehlt ein Key), ist der Rollout grün, aber der nächste nächtliche Lauf
# scheitert mit CreateContainerConfigError. Dieser Preflight fängt das VOR dem
# ``kubectl apply`` mit klarer Meldung ab.
#
# Prüft (im Namespace dms):
#   1. Secret dms-db-backup-secrets existiert.
#   2. Es enthält die Keys DJANGO_SECRET_KEY und POSTGRES_PASSWORD.
#   3. Sein POSTGRES_PASSWORD stimmt mit dms-secrets überein (PVC-gekoppelt).
#
# Best-effort bei fehlenden Leserechten: Kann die Deploy-Identität die Secrets
# NICHT lesen (RBAC ohne ``secrets: get``), wird nur GEWARNT statt zu blockieren –
# der Preflight soll den Rollout nie an fehlender Leseberechtigung aufhängen.
set -euo pipefail

NS="dms"
BK="dms-db-backup-secrets"
APP="dms-secrets"
REQUIRED_KEYS=(DJANGO_SECRET_KEY POSTGRES_PASSWORD)

# --- Lesbarkeit prüfen (Forbidden -> nur warnen) ---------------------------
if ! err=$(kubectl -n "$NS" get secret "$APP" -o jsonpath='{.metadata.name}' 2>&1); then
  case "$err" in
    *[Ff]orbidden*)
      echo "::warning::Preflight übersprungen – Deploy-Identität darf Secrets in '$NS' nicht lesen. dms-db-backup-secrets bitte manuell sicherstellen (secret.example.yaml)."
      exit 0
      ;;
    *)
      echo "::error::Preflight: dms-secrets nicht lesbar/vorhanden in '$NS': $err"
      exit 1
      ;;
  esac
fi

# --- 1) Existenz von dms-db-backup-secrets ---------------------------------
if ! kubectl -n "$NS" get secret "$BK" >/dev/null 2>&1; then
  echo "::error::Secret '$BK' fehlt im Namespace '$NS'. Der Backup-CronJob würde mit CreateContainerConfigError scheitern. Anlegen (secret.example.yaml) und erneut deployen."
  exit 1
fi

# --- 2) Pflicht-Keys vorhanden ---------------------------------------------
for key in "${REQUIRED_KEYS[@]}"; do
  val=$(kubectl -n "$NS" get secret "$BK" -o jsonpath="{.data.$key}" 2>/dev/null || true)
  if [ -z "$val" ]; then
    echo "::error::Secret '$BK' fehlt der Pflicht-Key '$key'."
    exit 1
  fi
done

# --- 3) POSTGRES_PASSWORD == dms-secrets (PVC-gekoppelt) --------------------
bk_pg=$(kubectl -n "$NS" get secret "$BK"  -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null || true)
app_pg=$(kubectl -n "$NS" get secret "$APP" -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null || true)
if [ -n "$app_pg" ] && [ "$bk_pg" != "$app_pg" ]; then
  echo "::error::POSTGRES_PASSWORD in '$BK' weicht von '$APP' ab. pg_dump würde scheitern (das Passwort ist an das postgres-data-PVC gekoppelt). Beide Secrets angleichen."
  exit 1
fi

echo "Preflight OK: '$BK' existiert, hat die Pflicht-Keys und passt zu '$APP'."
