#!/usr/bin/env sh
# Restore-Drill: holt das JÜNGSTE Offsite-Backup vom NAS (SSH/scp — dasselbe Ziel,
# das der Backup-CronJob beschreibt) und spielt es in eine WEGWERFBARE Postgres-
# Instanz + ein Temp-Verzeichnis ein, um die Wiederherstellbarkeit nachzuweisen.
# Die Produktion wird NICHT angefasst; das Offsite-Ziel wird nur gelesen.
# Restore-Drill: beweist, dass das jüngste Offsite-Backup wiederherstellbar ist.
#
# Muss zum Backup-CronJob (backup-cronjob.yaml) passen: dieser legt via rsync
# FLACHE Dateien db-<TS>.sql.gz und data-<TS>.tar.gz unter BACKUP_TARGET_PATH ab
# (kein Unterordner, keine SHA256SUMS-Datei). Der Drill liest exakt dieses Format.
# Der Drill ist absichtlich NICHT-destruktiv:
# - Produktions-Postgres wird nicht angefasst.
# - Das dms-data-PVC wird nicht beschrieben.
# - Die DB wird in einen temporären Postgres-Pod importiert.
# - Das /data-Archiv wird lokal in ein Temp-Verzeichnis entpackt.
#
# Voraussetzungen: ssh, scp, gzip, docker ODER podman.
# Zugangsdaten wie beim CronJob (Secret dms-backup-secrets) — per Env übergeben:
#   BACKUP_SSH_HOST      SSH-Zielhost (NAS)
#   BACKUP_SSH_USER      SSH-Benutzer
#   BACKUP_SSH_KEY       SSH Private Key (PEM, mehrzeilig)
#   BACKUP_TARGET_PATH   Zielverzeichnis auf dem NAS (z. B. /volume1/backups/dms)
# Standard: Zugangsdaten aus dem k8s Secret dms-backup-secrets lesen.
# Optional können BACKUP_* Variablen gesetzt werden, um andere Werte zu testen.
#
# Nutzung (Werte aus dem Secret ziehen und Drill lokal ausführen):
#   export BACKUP_SSH_HOST=nas.heimnetz.local BACKUP_SSH_USER=backup-user \
#          BACKUP_TARGET_PATH=/volume1/backups/dms
#   export BACKUP_SSH_KEY="$(cat ~/.ssh/dms-backup-key)"
# Nutzung auf einem k3s-Node mit kubectl:
#   ./deploy/k8s/restore-drill.sh
#
# Optional:
#   BACKUP_TS   fester Zeitstempel (Datei-Suffix) statt „jüngster"
#   BACKUP_TS=20260706-084501 ./deploy/k8s/restore-drill.sh

set -eu

: "${BACKUP_SSH_HOST:?BACKUP_SSH_HOST fehlt (siehe Secret dms-backup-secrets)}"
: "${BACKUP_SSH_USER:?BACKUP_SSH_USER fehlt (siehe Secret dms-backup-secrets)}"
: "${BACKUP_SSH_KEY:?BACKUP_SSH_KEY fehlt (siehe Secret dms-backup-secrets)}"
: "${BACKUP_TARGET_PATH:?BACKUP_TARGET_PATH fehlt (siehe Secret dms-backup-secrets)}"
PG_IMAGE="postgres:16-alpine"
CONTAINER="dms-restore-drill"
NAMESPACE="${NAMESPACE:-dms}"
SECRET_NAME="${SECRET_NAME:-dms-backup-secrets}"
PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
POD="dms-restore-drill-$(date +%s)"

# --- Container-Runtime ermitteln -------------------------------------------
if command -v docker >/dev/null 2>&1; then RT=docker
elif command -v podman >/dev/null 2>&1; then RT=podman
else echo "FEHLER: docker/podman nicht gefunden" >&2; exit 1; fi
for t in ssh scp gzip; do
  command -v "$t" >/dev/null 2>&1 || { echo "FEHLER: $t fehlt" >&2; exit 1; }
for tool in kubectl ssh gzip tar base64; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "FEHLER: $tool fehlt" >&2
    exit 1
  }
done

secret_value() {
  key="$1"
  kubectl -n "$NAMESPACE" get secret "$SECRET_NAME" \
    -o "jsonpath={.data.${key}}" | base64 -d
}

BACKUP_SSH_HOST="${BACKUP_SSH_HOST:-$(secret_value BACKUP_SSH_HOST)}"
BACKUP_SSH_PORT="${BACKUP_SSH_PORT:-$(secret_value BACKUP_SSH_PORT 2>/dev/null || printf '22')}"
BACKUP_SSH_USER="${BACKUP_SSH_USER:-$(secret_value BACKUP_SSH_USER)}"
BACKUP_TARGET_PATH="${BACKUP_TARGET_PATH:-$(secret_value BACKUP_TARGET_PATH)}"

: "${BACKUP_SSH_HOST:?BACKUP_SSH_HOST fehlt}"
: "${BACKUP_SSH_USER:?BACKUP_SSH_USER fehlt}"
: "${BACKUP_TARGET_PATH:?BACKUP_TARGET_PATH fehlt}"

WORK="$(mktemp -d)"
KEY="$WORK/ssh_key"

cleanup() {
  echo "[drill] Aufräumen ..."
  $RT rm -f "$CONTAINER" >/dev/null 2>&1 || true
  kubectl -n "$NAMESPACE" delete pod "$POD" --ignore-not-found=true >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

printf '%s\n' "$BACKUP_SSH_KEY" > "$KEY"
if [ "${BACKUP_SSH_KEY:-}" ]; then
  printf '%s\n' "$BACKUP_SSH_KEY" > "$KEY"
else
  secret_value BACKUP_SSH_KEY > "$KEY"
fi
chmod 600 "$KEY"
# StrictHostKeyChecking=no für Heimnetz (privates LAN); für Produktiv-/Cloud-
# Setup würde man den Host-Key vorab verifizieren.
SSH_OPTS="-i $KEY -o StrictHostKeyChecking=no"

SSH_OPTS="-p ${BACKUP_SSH_PORT:-22} -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o PreferredAuthentications=publickey -o StrictHostKeyChecking=no"
