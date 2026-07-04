#!/usr/bin/env sh
# Restore-Drill: spielt das jüngste Offsite-Backup in eine WEGWERFBARE Postgres-
# Instanz + ein Temp-Verzeichnis ein und verifiziert die Integrität. Die
# Produktion wird NICHT angefasst (Offsite-Ziel wird nur gelesen).
#
# Zweck: Nachweis, dass aus dem Backup tatsächlich wiederhergestellt werden kann.
#
# Voraussetzungen: rclone, docker ODER podman, gzip, sha256sum.
# Nutzung:
#   RCLONE_CONF=deploy/k8s/backup-secret.yaml ./deploy/k8s/restore-drill.sh
# Optionale Env:
#   RCLONE_REMOTE  (Default: offsite:dms-backup)
#   BACKUP_TS      (fester Zeitstempel-Ordner statt „jüngster")
set -eu

RCLONE_CONF="${RCLONE_CONF:-deploy/k8s/backup-secret.yaml}"
RCLONE_REMOTE="${RCLONE_REMOTE:-offsite:dms-backup}"
PG_IMAGE="postgres:16-alpine"
CONTAINER="dms-restore-drill"

# --- Container-Runtime ermitteln -------------------------------------------
if command -v docker >/dev/null 2>&1; then RT=docker
elif command -v podman >/dev/null 2>&1; then RT=podman
else echo "FEHLER: docker/podman nicht gefunden" >&2; exit 1; fi
command -v rclone >/dev/null 2>&1 || { echo "FEHLER: rclone fehlt" >&2; exit 1; }

WORK="$(mktemp -d)"
cleanup() {
  echo "[drill] Aufräumen ..."
  $RT rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

RC="rclone --config $RCLONE_CONF"

# --- 1. jüngstes Backup holen ----------------------------------------------
TS="${BACKUP_TS:-$($RC lsf --dirs-only "$RCLONE_REMOTE" | sort | tail -1 | tr -d /)}"
[ -n "$TS" ] || { echo "FEHLER: kein Backup unter $RCLONE_REMOTE gefunden" >&2; exit 1; }
echo "[drill] Backup-Zeitstempel: $TS"
$RC copy "$RCLONE_REMOTE/$TS" "$WORK/$TS"

# --- 2. Integrität prüfen ---------------------------------------------------
echo "[drill] Prüfsummen ..."
( cd "$WORK/$TS" && sha256sum -c SHA256SUMS )

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
echo "[drill] spiele db.sql.gz ein ..."
gunzip -c "$WORK/$TS/db.sql.gz" | $RT exec -i "$CONTAINER" psql -U dms -d dms >/dev/null
TABLES=$($RT exec "$CONTAINER" psql -U dms -d dms -tAc \
  "select count(*) from information_schema.tables where table_schema='public';")
DOCS=$($RT exec "$CONTAINER" psql -U dms -d dms -tAc \
  "select count(*) from documents_document;" 2>/dev/null || echo "?")
echo "[drill] DB wiederhergestellt: $TABLES Tabellen, documents_document=$DOCS Zeilen"

# --- 4. /data-Archiv entpacken + zählen ------------------------------------
echo "[drill] entpacke data.tar.gz ..."
mkdir -p "$WORK/data"
tar xzf "$WORK/$TS/data.tar.gz" -C "$WORK/data"
FILES=$(find "$WORK/data" -type f | wc -l | tr -d ' ')
echo "[drill] /data wiederhergestellt: $FILES Dateien in $(ls "$WORK/data" | tr '\n' ' ')"

echo ""
echo "==================== DRILL ERFOLGREICH ===================="
echo " Backup-TS      : $TS"
echo " DB-Tabellen    : $TABLES  (documents_document: $DOCS)"
echo " /data-Dateien  : $FILES"
echo " -> Ergebnis in docs/backup.md (Drill-Protokoll) eintragen."
echo "==========================================================="
