#!/usr/bin/env sh
# Restore-Drill: beweist, dass das jüngste Offsite-Backup wiederherstellbar ist.
#
# Der Drill ist absichtlich NICHT-destruktiv:
# - Produktions-Postgres wird nicht angefasst.
# - Das dms-data-PVC wird nicht beschrieben.
# - Die DB wird in einen temporären Postgres-Pod importiert.
# - Das /data-Archiv wird lokal in ein Temp-Verzeichnis entpackt.
#
# Standard: Zugangsdaten aus dem k8s Secret dms-backup-secrets lesen.
# Optional können BACKUP_* Variablen gesetzt werden, um andere Werte zu testen.
#
# Nutzung auf einem k3s-Node mit kubectl:
#   ./deploy/k8s/restore-drill.sh
#
# Optional:
#   BACKUP_TS=20260706-084501 ./deploy/k8s/restore-drill.sh

set -eu

NAMESPACE="${NAMESPACE:-dms}"
SECRET_NAME="${SECRET_NAME:-dms-backup-secrets}"
PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
POD="dms-restore-drill-$(date +%s)"

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

optional_secret_value() {
  key="$1"
  value="$(kubectl -n "$NAMESPACE" get secret "$SECRET_NAME" \
    -o "jsonpath={.data.${key}}" 2>/dev/null || true)"
  [ -n "$value" ] || return 1
  printf '%s' "$value" | base64 -d
}

BACKUP_SSH_HOST="${BACKUP_SSH_HOST:-$(secret_value BACKUP_SSH_HOST)}"
BACKUP_SSH_PORT="${BACKUP_SSH_PORT:-$(optional_secret_value BACKUP_SSH_PORT || printf '22')}"
BACKUP_SSH_USER="${BACKUP_SSH_USER:-$(secret_value BACKUP_SSH_USER)}"
BACKUP_TARGET_PATH="${BACKUP_TARGET_PATH:-$(secret_value BACKUP_TARGET_PATH)}"

: "${BACKUP_SSH_HOST:?BACKUP_SSH_HOST fehlt}"
: "${BACKUP_SSH_USER:?BACKUP_SSH_USER fehlt}"
: "${BACKUP_TARGET_PATH:?BACKUP_TARGET_PATH fehlt}"

WORK="$(mktemp -d)"
KEY="$WORK/ssh_key"

cleanup() {
  echo "[drill] Aufräumen ..."
  kubectl -n "$NAMESPACE" delete pod "$POD" --ignore-not-found=true >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

if [ "${BACKUP_SSH_KEY:-}" ]; then
  printf '%s\n' "$BACKUP_SSH_KEY" > "$KEY"
else
  secret_value BACKUP_SSH_KEY > "$KEY"
fi
chmod 600 "$KEY"

SSH_OPTS="-p ${BACKUP_SSH_PORT:-22} -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o PreferredAuthentications=publickey -o StrictHostKeyChecking=no"
REMOTE="${BACKUP_SSH_USER}@${BACKUP_SSH_HOST}"

echo "[drill] SSH-Ziel: ${REMOTE}:${BACKUP_TARGET_PATH} (Port ${BACKUP_SSH_PORT:-22})"
ssh $SSH_OPTS "$REMOTE" "echo SSH OK"

TS="${BACKUP_TS:-$(ssh $SSH_OPTS "$REMOTE" "ls -1 ${BACKUP_TARGET_PATH}/db-*.sql.gz" 2>/dev/null \
  | sed -n 's#.*/db-\(.*\)\.sql\.gz$#\1#p' | sort | tail -1)}"

[ -n "$TS" ] || {
  echo "FEHLER: kein db-*.sql.gz unter ${REMOTE}:${BACKUP_TARGET_PATH} gefunden" >&2
  exit 1
}

DB_REMOTE="${BACKUP_TARGET_PATH}/db-${TS}.sql.gz"
DATA_REMOTE="${BACKUP_TARGET_PATH}/data-${TS}.tar.gz"
DB_LOCAL="$WORK/db-${TS}.sql.gz"
DATA_LOCAL="$WORK/data-${TS}.tar.gz"

echo "[drill] Backup-Zeitstempel: $TS"
echo "[drill] lade DB-Artefakt per SSH ..."
ssh $SSH_OPTS "$REMOTE" "cat '${DB_REMOTE}'" > "$DB_LOCAL"

echo "[drill] lade /data-Artefakt per SSH ..."
ssh $SSH_OPTS "$REMOTE" "cat '${DATA_REMOTE}'" > "$DATA_LOCAL"

echo "[drill] prüfe gzip-Integrität ..."
gzip -t "$DB_LOCAL"
gzip -t "$DATA_LOCAL"

echo "[drill] starte temporären Postgres-Pod $POD ($PG_IMAGE) ..."
kubectl -n "$NAMESPACE" run "$POD" \
  --image="$PG_IMAGE" \
  --restart=Never \
  --env=POSTGRES_USER=dms \
  --env=POSTGRES_PASSWORD=drill \
  --env=POSTGRES_DB=dms \
  >/dev/null

echo "[drill] warte auf Pod ..."
kubectl -n "$NAMESPACE" wait --for=condition=Ready "pod/$POD" --timeout=120s >/dev/null

echo "[drill] warte auf PostgreSQL ..."
i=0
until kubectl -n "$NAMESPACE" exec "$POD" -- pg_isready -U dms >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 60 ] && {
    echo "FEHLER: temporärer Postgres wurde nicht bereit" >&2
    exit 1
  }
  sleep 1
done

echo "[drill] spiele db-${TS}.sql.gz in temporären Postgres ein ..."
gunzip -c "$DB_LOCAL" \
  | kubectl -n "$NAMESPACE" exec -i "$POD" -- \
      psql -v ON_ERROR_STOP=1 -U dms -d dms >/dev/null

TABLES="$(kubectl -n "$NAMESPACE" exec "$POD" -- psql -U dms -d dms -tAc \
  "select count(*) from information_schema.tables where table_schema='public';" | tr -d '[:space:]')"
DOCS="$(kubectl -n "$NAMESPACE" exec "$POD" -- psql -U dms -d dms -tAc \
  "select count(*) from documents_document;" 2>/dev/null | tr -d '[:space:]' || printf '?')"

echo "[drill] entpacke data-${TS}.tar.gz lokal ..."
mkdir -p "$WORK/data"
tar xzf "$DATA_LOCAL" -C "$WORK/data"
FILES="$(find "$WORK/data" -type f | wc -l | tr -d '[:space:]')"
TOP_LEVEL="$(find "$WORK/data" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort | tr '\n' ' ')"

echo ""
echo "==================== RESTORE-DRILL ERFOLGREICH ===================="
echo " Backup-TS       : $TS"
echo " DB-Tabellen     : $TABLES"
echo " Dokumente       : $DOCS"
echo " /data-Dateien   : $FILES"
echo " /data-Ordner    : $TOP_LEVEL"
echo " Produktion      : NICHT verändert"
echo "==================================================================="
