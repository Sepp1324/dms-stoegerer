
type: Opaque
stringData:
  BACKUP_SSH_HOST: "nas.heimnetz.local"
  BACKUP_SSH_PORT: "260"
  BACKUP_SSH_USER: "backup-user"
  BACKUP_SSH_KEY: |
    -----BEGIN OPENSSH PRIVATE KEY-----
kubectl logs -n dms job/$LAST_JOB

# Erfolgreiche Backups auf NAS prüfen
ssh backup-user@nas.heimnetz.local "ls -lh /volume1/backups/dms/"
ssh -p 260 backup-user@nas.heimnetz.local "ls -lh /volume1/backups/dms/"
```

## Restore-Drill testen

Der Restore-Drill ist der Standardtest nach Backup-Änderungen. Er ist **nicht destruktiv**:
- Produktions-Postgres wird nicht verändert.
- Das produktive `dms-data` PVC wird nicht beschrieben.
- Die Datenbank wird in einen temporären PostgreSQL-Pod importiert.
- Das `/data`-Archiv wird lokal in ein temporäres Verzeichnis entpackt und geprüft.

```bash
cd ~/pods/DMS

# nutzt standardmäßig Namespace dms und Secret dms-backup-secrets
./deploy/k8s/restore-drill.sh
```

## Restore-Verfahren
Ein bestimmtes Backup testen:

```bash
BACKUP_TS=20260706-084501 ./deploy/k8s/restore-drill.sh
```

Erwarteter Erfolgsfall:

```text
==================== RESTORE-DRILL ERFOLGREICH ====================
 Backup-TS       : 20260706-084501
 DB-Tabellen     : <anzahl>
 Dokumente       : <anzahl>
 /data-Dateien   : <anzahl>
 Produktion      : NICHT verändert
===================================================================
```

Wenn dieser Drill fehlschlägt, gilt das Backup nicht als verifiziert.

## Produktiv-Restore-Verfahren

### Voraussetzungen
- Zugriff auf das NAS (SSH)
- kubectl-Zugriff auf den k3s-Cluster

```bash
# Verfügbare Backups auflisten
ssh backup-user@nas.heimnetz.local "ls -lht /volume1/backups/dms/ | head -20"
ssh -p 260 backup-user@nas.heimnetz.local "ls -lht /volume1/backups/dms/ | head -20"

# Gewünschtes Backup identifizieren (z. B. vom 2026-07-03)
BACKUP_DATE="20260703-020015"

# Backup lokal herunterladen
mkdir -p ~/dms-restore
cd ~/dms-restore
scp backup-user@nas.heimnetz.local:/volume1/backups/dms/db-${BACKUP_DATE}.sql.gz .
scp backup-user@nas.heimnetz.local:/volume1/backups/dms/data-${BACKUP_DATE}.tar.gz .
ssh -p 260 backup-user@nas.heimnetz.local "cat /volume1/backups/dms/db-${BACKUP_DATE}.sql.gz" > "db-${BACKUP_DATE}.sql.gz"
ssh -p 260 backup-user@nas.heimnetz.local "cat /volume1/backups/dms/data-${BACKUP_DATE}.tar.gz" > "data-${BACKUP_DATE}.tar.gz"

gzip -t "db-${BACKUP_DATE}.sql.gz"
gzip -t "data-${BACKUP_DATE}.tar.gz"
```

### Schritt 2: Anwendung stoppen

```bash
# Backend und Worker herunterskalieren (verhindert DB-Zugriffe während Restore)
kubectl scale deployment -n dms backend --replicas=0
kubectl scale deployment -n dms celery-worker --replicas=0
kubectl scale deployment -n dms worker --replicas=0

# Warten bis Pods beendet sind
kubectl wait --for=delete pod -n dms -l app=backend --timeout=60s
kubectl wait --for=delete pod -n dms -l app=celery-worker --timeout=60s
kubectl wait --for=delete pod -n dms -l app=worker --timeout=60s
```

### Schritt 3: Datenbank wiederherstellen
```bash
# Backend und Worker hochfahren
kubectl scale deployment -n dms backend --replicas=1
kubectl scale deployment -n dms celery-worker --replicas=1
kubectl scale deployment -n dms worker --replicas=1

# Pods hochkommen lassen
kubectl wait --for=condition=ready pod -n dms -l app=backend --timeout=120s
kubectl wait --for=condition=ready pod -n dms -l app=celery-worker --timeout=120s

## Troubleshooting: „Letztes erfolgreiches Backup ist X Tage her"

Der Monitor meldet fehlende Erfolge, aber der CronJob läuft (`SUSPEND False`, täglich
02:00). Symptomatisch scheitern **alle** Läufe seit einem Stichtag.

### 1. Ist es ein Hänger oder ein Fehler?

```bash
kubectl -n dms get cronjob backup -o wide          # ACTIVE > 0 ⇒ ein Lauf hängt
kubectl -n dms get jobs -l app=backup --sort-by=.metadata.creationTimestamp | tail -8
```

- **`ACTIVE 1` + alter Pod:** Ein hängender Job blockiert wegen
  `concurrencyPolicy: Forbid` **alle** folgenden Läufe. Job löschen. *(Seit der
  Härtung greift `activeDeadlineSeconds: 3600` – ein Hänger wird nach 1 h beendet.)*
- **Alle Jobs `Failed`:** echter Fehler → weiter mit 2.

> `failedJobsHistoryLimit: 3` – ältere Fehlläufe (und ihre Pods) sind weg. Die Logs
> alter Läufe existieren dann nicht mehr; deshalb einen **manuellen Lauf** starten.

### 2. Manuellen Lauf starten und live mitlesen

```bash
kubectl -n dms create job --from=cronjob/backup backup-check-$(date +%s)
kubectl -n dms logs -f -l app=backup --tail=100 --prefix
```

### 3. Häufigster Fall: SSH-Key von der NAS abgelehnt

```
--- SSH-Vorabprüfung: dms_backup@192.168.1.101 (Port 260)
Permission denied, please try again.
FEHLER: SSH-Auth zur NAS fehlgeschlagen – pg_dump/tar übersprungen.
```

`pg_dump`/`tar` sind dann **gesund** – nur die Auslagerung scheitert. Typische
Ursache: ein **Synology-DSM-Update** setzt `authorized_keys` oder die Home-Rechte
zurück.

**Fix (auf der NAS):**

```bash
# 1) Passenden Public-Key aus dem Secret ableiten (lokal, danach löschen!)
kubectl -n dms get secret dms-backup-secrets -o jsonpath='{.data.BACKUP_SSH_KEY}' \
  | base64 -d > /tmp/dms_backup_key
chmod 600 /tmp/dms_backup_key
ssh-keygen -y -f /tmp/dms_backup_key      # → dieser Public-Key muss auf die NAS
rm -f /tmp/dms_backup_key

# 2) Auf der Synology (als Admin) prüfen:
#    - User aktiv, hat ein Home (DSM → „Benutzer-Home" aktiviert), SSH auf Port 260
#    - Public-Key steht in ~/.ssh/authorized_keys
#    - Rechte (Synology ist streng):
chmod 700 ~/.ssh                 # bzw. Home 0711
chmod 600 ~/.ssh/authorized_keys # Home NICHT group-/world-writable
#    - /etc/ssh/sshd_config: PubkeyAuthentication yes, User nicht via AllowUsers gesperrt
```

Danach erneut einen manuellen Lauf starten (Schritt 2) – er muss bis
„=== Backup abgeschlossen" laufen.

### Härtung (bereits aktiv)

- **SSH-Vorabprüfung ganz am Anfang:** Ein abgelehnter Key scheitert in Sekunden mit
  klarer Statusmeldung im Monitoring – statt erst nach `pg_dump` + `tar` mit einem
  generischen Fehler.
- **`activeDeadlineSeconds: 3600`** – ein hängender Lauf kann nie wieder tagelang alle
  weiteren blockieren.
- `backoffLimit: 2`, `startingDeadlineSeconds: 600`.
