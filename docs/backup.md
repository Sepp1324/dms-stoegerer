
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
