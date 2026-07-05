# STOAA-472/474 — Postgres-Auth-Fehler: Diagnose & Fix (Owner-Runbook)

## Symptom
Backend-Rollout hängt: der `migrate`-Init-Container des Backend-Pods bricht ab mit

```
django.db.utils.OperationalError: FATAL: password authentication failed for user "dms"
```

Der Backend-Pod wird dadurch nie `Ready`, jeder neue Backend-Rollout (Reminder-Route
STOAA-454/456, Consume-Move-Verifikation STOAA-433) bleibt blockiert.

## Ursache
`POSTGRES_PASSWORD` im Secret `dms-secrets` stimmt nicht mit dem Passwort überein, mit dem
die `postgres-data`-PVC **erst-initialisiert** wurde. Postgres setzt das User-Passwort nur
beim ersten Start auf leerem Datenverzeichnis. Wird das Secret danach neu generiert/rotiert,
behält die PVC das alte Passwort → Mismatch.

## Fix (datensicher, ~2–5 Min., kubectl nötig)
Das DB-User-Passwort im **laufenden** Postgres-Pod auf den aktuellen Secret-Wert setzen.
Kein PVC-Delete, kein Datenverlust. Die lokale `psql`-Verbindung im Pod nutzt trust-Auth
über den Unix-Socket und funktioniert daher trotz des Mismatches.

### Empfohlen: Skript
Auf dem self-hosted Runner (kubectl-Zugriff):

```bash
cd dms-stoegerer
git pull            # Skript liegt seit dieser Änderung auf main
./scripts/fix-postgres-password.sh
```
Das Skript zeigt zuerst die Diagnose (Pod-/PVC-/Log-Status) und fragt dann, ob das
Passwort gesetzt werden soll (interaktiv, `y/N`). Danach startet es den Backend-Pod neu
und zeigt die migrate-Init-Logs zur Verifikation.

### Manuell (falls kein interaktives TTY)
```bash
PW=$(kubectl -n dms get secret dms-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
kubectl -n dms exec -i deploy/postgres -- \
  psql -U dms -v ON_ERROR_STOP=1 -v pw="$PW" -c "ALTER USER dms PASSWORD :'pw';"
kubectl -n dms delete pod -l app=backend    # migrate-Init läuft neu
kubectl -n dms rollout status deploy/backend
```

## Verifikation (Erfolg)
- `kubectl -n dms logs <backend-pod> -c migrate` → keine `password authentication failed`.
- Backend-Pod `Running`/`Ready`, `kubectl -n dms rollout status deploy/backend` grün.
- Danach: Reminder-Route live prüfen (STOAA-456) und Consume-Move (STOAA-433).

## Durable-Fix (Rezidiv verhindern)
Damit der Mismatch nicht bei jeder Secret-Rotation wiederkehrt: `POSTGRES_PASSWORD` als
**stabilen, versionierten Wert** führen (fester Secret-Wert / Sealed-Secret), nicht per
`secretGenerator` mit wechselndem Hash-Suffix neu erzeugen. Andernfalls muss dieser Fix
nach jeder Rotation erneut laufen.
