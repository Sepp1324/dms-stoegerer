#!/usr/bin/env sh
# Restore-Drill: holt das JÜNGSTE Offsite-Backup vom NAS (SSH/scp — dasselbe Ziel,
# das der Backup-CronJob beschreibt) und spielt es in eine WEGWERFBARE Postgres-
# Instanz + ein Temp-Verzeichnis ein, um die Wiederherstellbarkeit nachzuweisen.
# Die Produktion wird NICHT angefasst; das Offsite-Ziel wird nur gelesen.
#
# Muss zum Backup-CronJob (backup-cronjob.yaml) passen: dieser legt via rsync
# FLACHE Dateien db-<TS>.sql.gz und data-<TS>.tar.gz unter BACKUP_TARGET_PATH ab
# (kein Unterordner, keine SHA256SUMS-Datei). Der Drill liest exakt dieses Format.
#
# Voraussetzungen: ssh, scp, gzip, docker ODER podman.
# Zugangsdaten wie beim CronJob (Secret dms-backup-secrets) — per Env übergeben:
#   BACKUP_SSH_HOST      SSH-Zielhost (NAS)
#   BACKUP_SSH_USER      SSH-Benutzer
#   BACKUP_SSH_KEY       SSH Private Key (PEM, mehrzeilig)
#   BACKUP_TARGET_PATH   Zielverzeichnis auf dem NAS (z. B. /volume1/backups/dms)
#
# Nutzung (Werte aus dem Secret ziehen und Drill lokal ausführen):
#   export BACKUP_SSH_HOST=nas.heimnetz.local BACKUP_SSH_USER=backup-user \
#          BACKUP_TARGET_PATH=/volume1/backups/dms
#   export BACKUP_SSH_KEY="$(cat ~/.ssh/dms-backup-key)"
#   ./deploy/k8s/restore-drill.sh
# Optional:
#   BACKUP_TS   fester Zeitstempel (Datei-Suffix) statt „jüngster"
set -eu

: "${BACKUP_SSH_HOST:?BACKUP_SSH_HOST fehlt (siehe Secret dms-backup-secrets)}"
: "${BACKUP_SSH_USER:?BACKUP_SSH_USER fehlt (siehe Secret dms-backup-secrets)}"
: "${BACKUP_SSH_KEY:?BACKUP_SSH_KEY fehlt (siehe Secret dms-backup-secrets)}"
: "${BACKUP_TARGET_PATH:?BACKUP_TARGET_PATH fehlt (siehe Secret dms-backup-secrets)}"
PG_IMAGE="postgres:16-alpine"
CONTAINER="dms-restore-drill"

# --- Container-Runtime ermitteln -------------------------------------------
if command -v docker >/dev/null 2>&1; then RT=docker
elif command -v podman >/dev/null 2>&1; then RT=podman
else echo "FEHLER: docker/podman nicht gefunden" >&2; exit 1; fi
for t in ssh scp gzip; do
  command -v "$t" >/dev/null 2>&1 || { echo "FEHLER: $t fehlt" >&2; exit 1; }
done

WORK="$(mktemp -d)"
KEY="$WORK/ssh_key"
cleanup() {
  echo "[drill] Aufräumen ..."
  $RT rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

printf '%s\n' "$BACKUP_SSH_KEY" > "$KEY"
chmod 600 "$KEY"
# StrictHostKeyChecking=no für Heimnetz (privates LAN); für Produktiv-/Cloud-
# Setup würde man den Host-Key vorab verifizieren.
SSH_OPTS="-i $KEY -o StrictHostKeyChecking=no"
REMOTE="${BACKUP_SSH_USER}@${BACKUP_SSH_HOST}"

# --- 1. jüngstes Backup ermitteln (Datei-Suffix db-<TS>.sql.gz, lexikal. sortiert) ---
TS="${BACKUP_TS:-$(ssh $SSH_OPTS "$REMOTE" "ls -1 ${BACKUP_TARGET_PATH}/db-*.sql.gz" 2>/dev/null \
  | sed -n 's#.*/db-\(.*\)\.sql\.gz$#\1#p' | sort | tail -1)}"
[ -n "$TS" ] || { echo "FEHLER: kein db-*.sql.gz unter ${REMOTE}:${BACKUP_TARGET_PATH} gefunden" >&2; exit 1; }
echo "[drill] Backup-Zeitstempel: $TS"

DB_REMOTE="${BACKUP_TARGET_PATH}/db-${TS}.sql.gz"
DATA_REMOTE="${BACKUP_TARGET_PATH}/data-${TS}.tar.gz"

# --- 2. herunterladen + Integrität (gzip -t) -------------------------------
echo "[drill] lade Artefakte ..."
scp $SSH_OPTS "${REMOTE}:${DB_REMOTE}" "$WORK/"
scp $SSH_OPTS "${REMOTE}:${DATA_REMOTE}" "$WORK/"
echo "[drill] prüfe gzip-Integrität ..."
gzip -t "$WORK/db-${TS}.sql.gz"
gzip -t "$WORK/data-${TS}.tar.gz"

# --- 3. DB in Wegwerf-Postgres einspielen ----------------------------------
echo "[drill] starte Wegwerf-Postgres ($PG_IMAGE) ..."
$RT rm -f "$CONTAINER" >/dev/null 2>&1 || true
$RT run -d --name "$CONTAINER" -e POSTGRES_USER=dms -e POSTGRES_PASSWORD=drill \
  -e POSTGRES_DB=dms "$PG_IMAGE" >/dev/null
echo "[drill] warte auf DB ..."
i=0; until $RT exec "$CONTAINER" pg_isready -U dms >/dev/null 2>&1; do
  i=$((i+1)); [ "$i" -gt 30 ] && { echo "FEHLER: DB nicht bereit" >&2; exit 1; }
  sleep 1
done
echo "[drill] spiele db-${TS}.sql.gz ein ..."
gunzip -c "$WORK/db-${TS}.sql.gz" \
  | $RT exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U dms -d dms >/dev/null
TABLES=$($RT exec "$CONTAINER" psql -U dms -d dms -tAc \
  "select count(*) from information_schema.tables where table_schema='public';")
DOCS=$($RT exec "$CONTAINER" psql -U dms -d dms -tAc \
  "select count(*) from documents_document;" 2>/dev/null || echo "?")
echo "[drill] DB wiederhergestellt: $TABLES Tabellen, documents_document=$DOCS Zeilen"

# --- 4. /data-Archiv entpacken + zählen ------------------------------------
echo "[drill] entpacke data-${TS}.tar.gz ..."
mkdir -p "$WORK/data"
tar xzf "$WORK/data-${TS}.tar.gz" -C "$WORK/data"
FILES=$(find "$WORK/data" -type f | wc -l | tr -d ' ')
echo "[drill] /data wiederhergestellt: $FILES Dateien in $(ls "$WORK/data" | tr '\n' ' ')"

echo ""
echo "==================== DRILL ERFOLGREICH ===================="
echo " Backup-TS      : $TS"
echo " DB-Tabellen    : $TABLES  (documents_document: $DOCS)"
echo " /data-Dateien  : $FILES"
echo " -> Ergebnis in docs/backup.md (Restore-Test-Protokoll) eintragen."
echo "==========================================================="
