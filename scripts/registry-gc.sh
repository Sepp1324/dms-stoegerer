#!/usr/bin/env bash
# Garbage-Collect der Container-Registry. Läuft AUF DEM REGISTRY-HOST (nicht auf
# dem Runner), als root, am besten wöchentlich per Cron – siehe docs/ci-cd.md §3c.
#
# scripts/registry-prune.py (läuft nach jedem Deploy) entfernt nur die Verweise
# auf alte Images. Der Plattenplatz wird erst frei, wenn die Registry die nicht
# mehr referenzierten Layer wegräumt – das macht dieser Lauf.
#
# Währenddessen darf nicht gepusht werden: ein gerade hochgeladener, noch nicht
# referenzierter Layer würde mit entfernt. Deshalb zu einer Zeit einplanen, in
# der sicher kein Deploy läuft (nachts). Der Neustart danach ist nötig, weil die
# Registry entfernte Layer sonst weiter als vorhanden führt (Cache im Speicher)
# und spätere Pushes dieser Layer überspringt.
#
# Anpassbar über Umgebungsvariablen:
#   REGISTRY_CONTAINER  Name des Containers            (Default: registry)
#   REGISTRY_CONFIG     Konfigdatei IM Container       (Default: /etc/docker/registry/config.yml)
#   REGISTRY_DATA_DIR   Datenverzeichnis auf dem Host  (Default: /opt/registry; nur für die df-Ausgabe)
set -euo pipefail

CONTAINER="${REGISTRY_CONTAINER:-registry}"
CONFIG="${REGISTRY_CONFIG:-/etc/docker/registry/config.yml}"
DATA_DIR="${REGISTRY_DATA_DIR:-/opt/registry}"

echo "== $(date '+%Y-%m-%d %H:%M:%S') Registry-Garbage-Collect (Container: ${CONTAINER})"
echo "-- Belegung vorher:"
df -h "$DATA_DIR" || true

# Die Ausgabe nennt jeden einzelnen Layer – nur zählen, was entfernt wurde.
if ! out="$(docker exec "$CONTAINER" registry garbage-collect "$CONFIG" 2>&1)"; then
  echo "$out" | tail -n 20
  echo "FEHLER: Garbage-Collect fehlgeschlagen – Container NICHT neu gestartet." >&2
  exit 1
fi
removed="$(grep -c 'eligible for deletion' <<<"$out" || true)"
echo "-- ${removed} Layer/Manifeste entfernt."

docker restart "$CONTAINER" >/dev/null
echo "-- Container neu gestartet. Belegung nachher:"
df -h "$DATA_DIR" || true
