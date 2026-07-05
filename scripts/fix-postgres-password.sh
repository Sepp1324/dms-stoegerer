#!/bin/bash
# STOAA-472/474: Diagnose und datensicherer Fix für Postgres-Auth-Fehler
#
# Symptom:
#   Backend-migrate-Init-Container schlägt fehl mit
#   "FATAL: password authentication failed for user 'dms'"
#   -> Backend-Pod wird nie Ready -> Rollout (Reminder-Route, Consume-Fixes) blockiert.
#
# Ursache:
#   POSTGRES_PASSWORD im Secret 'dms-secrets' != Passwort, mit dem die
#   postgres-data-PVC initial angelegt wurde. Postgres setzt das Passwort NUR
#   bei der Erst-Initialisierung (leeres Datenverzeichnis). Wird das Secret
#   danach rotiert, bleibt das PVC beim alten Passwort.
#
# Fix (datensicher): DB-User-Passwort im LAUFENDEN Pod auf den Secret-Wert setzen.
#   - Kein PVC-Delete, kein Datenverlust.
#   - Lokale psql-Verbindung im Pod nutzt trust-Auth (Unix-Socket) und
#     funktioniert daher trotz des Passwort-Mismatches.
#
# Muss auf dem self-hosted Runner mit kubectl-Zugriff ausgeführt werden.

set -euo pipefail

NS="dms"

echo "=== STOAA-472/474: Postgres-Passwort-Diagnose und -Fix ==="
echo ""

# 1. Secret-Wert auslesen
echo "1. Aktuelles POSTGRES_PASSWORD aus Secret 'dms-secrets':"
CURRENT_SECRET_PASSWORD=$(kubectl -n "$NS" get secret dms-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
echo "   Länge: ${#CURRENT_SECRET_PASSWORD} Zeichen"
echo "   Erste 4 Zeichen: ${CURRENT_SECRET_PASSWORD:0:4}***"
echo ""

# 2. Postgres-Pod-Status
echo "2. Postgres-Pod-Status:"
kubectl -n "$NS" get pods -l app=postgres -o wide
echo ""

# 3. Postgres-PVC
echo "3. Postgres-PVC (postgres-data):"
kubectl -n "$NS" get pvc postgres-data
echo ""

# 4. Backend-Pod-Status (zeigt migrate-Init-Fehler)
echo "4. Backend-Pod-Status (zeigt migrate-Init-Container-Fehler):"
kubectl -n "$NS" get pods -l app=backend -o wide
echo ""

# 5. Postgres-Logs (zeigt, ob DB neu initialisiert wurde oder existiert)
echo "5. Postgres-Container-Logs (letzte 20 Zeilen):"
POSTGRES_POD=$(kubectl -n "$NS" get pods -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl -n "$NS" logs "$POSTGRES_POD" --tail=20
echo ""

# 6. Backend-migrate-Init-Logs (zeigt Auth-Fehler)
echo "6. Backend migrate-Init-Container-Logs (zeigt Auth-Fehler):"
BACKEND_POD=$(kubectl -n "$NS" get pods -l app=backend -o jsonpath='{.items[0].metadata.name}')
kubectl -n "$NS" logs "$BACKEND_POD" -c migrate --tail=30 || echo "   (Init-Container noch nicht gestartet oder bereits beendet)"
echo ""

echo "=== Diagnose abgeschlossen ==="
echo ""
echo "Setzt das DB-User-Passwort im laufenden Postgres-Pod auf den Secret-Wert."
echo "psql-Variable :'pw' quotet den Wert sicher (auch bei Sonderzeichen)."
echo ""
read -p "Möchtest du das Passwort JETZT setzen? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Setze Passwort für DB-User 'dms'..."
    # Passwort als psql-Variable übergeben (-v) statt in den SQL-String zu interpolieren.
    # :'pw' sorgt für korrektes, injection-sicheres Quoting des Wertes.
    kubectl -n "$NS" exec -i deploy/postgres -- \
        psql -U dms -v ON_ERROR_STOP=1 -v pw="$CURRENT_SECRET_PASSWORD" \
        -c "ALTER USER dms PASSWORD :'pw';"
    echo "✓ Passwort gesetzt."
    echo ""
    echo "Backend-Pod neu starten, damit migrate-Init erneut läuft:"
    kubectl -n "$NS" delete pod -l app=backend
    echo "✓ Backend-Pod gelöscht (wird automatisch neu erstellt)."
    echo ""
    echo "Warte 10 Sekunden, dann neuer Pod-Status..."
    sleep 10
    kubectl -n "$NS" get pods -l app=backend -o wide
    echo ""
    echo "migrate-Init-Logs des neuen Pods:"
    sleep 5
    NEW_BACKEND_POD=$(kubectl -n "$NS" get pods -l app=backend -o jsonpath='{.items[0].metadata.name}')
    kubectl -n "$NS" logs "$NEW_BACKEND_POD" -c migrate --tail=30 || echo "   (Init noch nicht gestartet)"
    echo ""
    echo "Erwartung: keine 'password authentication failed'-Meldung mehr,"
    echo "Backend-Pod geht in Running/Ready."
else
    echo "Abgebrochen. Kein Passwort gesetzt."
fi
