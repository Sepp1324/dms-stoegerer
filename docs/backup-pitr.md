# Konsistente Backups über PostgreSQL-PITR + Datei-Snapshot

**Status:** Design/Runbook. Die PostgreSQL-Konfigurationsänderungen sind bewusst
**nicht** Teil von `deploy/k8s/base` – ein automatisch ausgerolltes
`archive_mode=on` ohne erreichbares Archivziel lässt WAL auf dem (kleinen,
5 GiB) `postgres-data`-PVC auflaufen, bis die DB stehen bleibt. Der Operator
richtet PITR einmalig ein und validiert es am Cluster, bevor es scharf geschaltet
wird.

## Warum (Befund: Snapshot + pg_dump sind kein gemeinsamer Zeitpunkt)

Das snapshot-basierte Backup snapshottet zuerst `/data` und dumpt danach die
**weiterlaufende** Datenbank. Eine zwischen beiden Schritten neu angelegte oder
gelöschte Version erzeugt ein inkonsistentes Restore-Paar (DB verweist auf eine
Datei, die nicht im Snapshot ist – oder umgekehrt). Reihenfolge-Tausch behebt das
nicht; er dreht die Inkonsistenz nur um.

**Lösung ohne globale Schreibsperre:** Die Datenbank wird per **PITR** (Point-in-
Time-Recovery) auf **genau den Zeitpunkt des Datei-Snapshots** zurückgespielt.
Dann stammen Dateien UND DB-Zustand vom selben Moment – konsistent per
Konstruktion, ohne Uploads/Verarbeitung/Löschung zu blockieren.

```
  t0 ── kontinuierliches WAL-Archiv ───────────────────────────►  (DB, laufend)
                 │
        t=T  VolumeSnapshot(dms-data)   ← konsistenter Datei-Stand zum Zeitpunkt T
                 │
  Restore:  Basebackup + WAL bis exакt T   +   Dateien aus Snapshot(T)
            └────────────── konsistentes Paar ──────────────┘
```

## Bausteine

### 1. WAL-Archivierung (kontinuierlich) auf die NAS

`postgresql.conf`-Overrides (als `-c`-Args oder gemountete Conf am
`pgvector/pgvector:pg16`-Container). `wal_level=replica` ist in PG16 bereits
Default; nötig sind:

```
archive_mode = on
archive_timeout = 300          # spätestens alle 5 min ein WAL-Segment schließen
archive_command = '/scripts/archive_wal.sh %p %f'
```

`archive_wal.sh` überträgt ein fertiges WAL-Segment per scp auf die NAS (gleiche
SSH-Identität wie das Datei-Backup, `dms-backup-secrets`). **Der Exit-Code MUSS
den Erfolg widerspiegeln** – bei Fehlschlag behält Postgres das Segment und
versucht erneut (sonst Datenverlust). Beispiel:

```sh
#!/bin/sh
set -e
WAL_PATH="$1"; WAL_NAME="$2"
exec scp -q -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/ssh/known_hosts -i /ssh/id \
  "${WAL_PATH}" "${BACKUP_SSH_USER}@${BACKUP_SSH_HOST}:${WAL_ARCHIVE_PATH}/${WAL_NAME}"
```

> **PVC-Risiko:** Läuft `archive_command` dauerhaft ins Leere, stapeln sich WAL in
> `pg_wal/` auf dem 5-GiB-`postgres-data`-PVC → DB-Stopp. Vor dem Scharfschalten:
> Archivziel testen, `postgres-data` großzügiger dimensionieren und
> `pg_stat_archiver` (`failed_count`) überwachen.

### 2. Periodisches Basebackup

Ein wöchentliches `pg_basebackup` (eigener, suspendiert ausgelieferter CronJob
oder manuell) auf die NAS bildet den Startpunkt; die WAL-Kette dazwischen erlaubt
Recovery auf jeden Zeitpunkt.

```
pg_basebackup -h postgres -U dms -D - -Ft -z -Xnone > base-$(date +%F).tar.gz
```

### 3. Datei-Snapshot mit Zeitstempel

`backup-snapshot-cronjob.yaml` snapshottet `/data` und protokolliert den
Snapshot-Zeitpunkt (`SNAP_TIMESTAMP`) über `record_backup_status`. Dieser
Zeitstempel ist das **Recovery-Ziel** der DB.

## Restore (konsistentes Paar)

1. Dateien aus dem Snapshot/`data-<TS>.tar.gz` nach `/data` zurückspielen.
2. DB aus dem letzten Basebackup VOR `TS` wiederherstellen.
3. `recovery.signal` setzen + `restore_command` (WAL von der NAS holen) +
   **`recovery_target_time = '<TS>'`** in `postgresql.conf`.
4. Postgres starten – spielt WAL bis exakt `TS` und stoppt (`recovery_target_action=promote`).

Ergebnis: `/data` und DB stammen vom selben Zeitpunkt `TS`.

## Übergang / Verhältnis zum bestehenden Backup

* Das aktive `backup-cronjob.yaml` (live-tar + pg_dump) bleibt der Ist-Zustand,
  bis PITR am Cluster validiert ist.
* Ist PITR aktiv, wird der **DB-Dump im Snapshot-Helfer überflüssig** – der Helfer
  reduziert sich auf „`/data` tarren + Zeitstempel" und braucht dann weder
  `DJANGO_SECRET_KEY` noch `POSTGRES_PASSWORD` (die Least-Privilege-Fläche
  schrumpft weiter).
* Erst wenn ein Restore-Probelauf (Basebackup + WAL bis `TS` + Snapshot) ein
  konsistentes Paar liefert, das alte live-tar-Backup außer Dienst nehmen.
