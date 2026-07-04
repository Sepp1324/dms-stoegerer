# Backup & Restore

Gesichert werden **zwei** Dinge, die zusammengehören:

| Was | Inhalt | Quelle |
|---|---|---|
| **Datenbank** | Metadaten, Hash-Kette, Audit-Trail, Versionen | `pg_dump` der DB `dms` über den `postgres`-Service |
| **`/data`** | Datei-Ablage: `originals/`, `archive/`, `thumbnails/` | `tar` der RWO-PVC `dms-data` |

> Beides gehört zum **gleichen Zeitpunkt** zusammen: Die DB verweist per Pfad auf
> Dateien in `/data`. Der CronJob sichert beide in einem Lauf. `consume/` wird
> bewusst **nicht** gesichert – es ist transienter Eingang (Scanner/Mail), keine
> revisionssichere Ablage.

Das automatisierte, offsite gesicherte Backup läuft als k8s-**CronJob**
`dms-backup` (`deploy/k8s/backup-cronjob.yaml`), täglich 02:30 (Europe/Vienna).

## 1. Architektur

```
CronJob dms-backup (Node = Backend-Node, podAffinity)
├─ initContainer "dump"  (postgres:16-alpine)
│    pg_dump dms  ──▶ /staging/<TS>/db.sql.gz     (+ gzip -t Integritätscheck)
│    tar /data    ──▶ /staging/<TS>/data.tar.gz   (originals archive thumbnails)
│    sha256sum    ──▶ /staging/<TS>/SHA256SUMS
└─ container "upload"    (rclone/rclone)
     rclone copy  /staging/<TS>  ──▶  $RCLONE_REMOTE/<TS>   (offsite)
     rclone check (Prüfsummen-Verifikation, sonst Job = Failed)
     Retention: nur die letzten N (BACKUP_RETENTION) Zeitstempel-Ordner behalten
```

**Warum rclone?** Ein Transport, viele Ziele – die Zugangsdaten liegen sauber in
genau einem gitignored Secret. Default-Empfehlung **kostenlos**: SFTP auf das
vorhandene NAS. Ohne Manifest-Änderung sind später S3/MinIO/Cloud möglich (echte
Cloud-Ziele = laufende Kosten → vorher mit CEO/Eigentümer abstimmen).

**Warum read-only + podAffinity?** `dms-data` ist RWO (ReadWriteOnce) und nur auf
dem Node des Backends mountbar; der Backup-Pod wird per `podAffinity` dorthin
geplant und mountet `/data` **read-only** (verändert die Ablage nie).

**Robustheit / kein halbes Artefakt gilt als gültig:**
`set -eu`; der Dump wird ohne Pipe geschrieben (ein `pg_dump`-Fehler bricht direkt
ab, statt ein leeres gzip als „gültig" durchzureichen); `gzip -t` prüft beide
Archive; `rclone check` verifiziert die Offsite-Kopie per Prüfsumme. Jeder Fehler
→ Job-Status **Failed** (sichtbar in `kubectl -n dms get cronjob,jobs`).

## 2. Einmalige Einrichtung (Owner)

Das Offsite-Ziel + Zugangsdaten stehen **nicht** im Repo. Der Owner setzt sie
einmalig über ein gitignored Secret:

```bash
cd deploy/k8s
cp backup-secret.example.yaml backup-secret.yaml
#   rclone.conf-Block mit echtem Ziel + Credentials füllen.
#   SFTP-Passwort NICHT im Klartext: mit  rclone obscure 'PASSWORT'  erzeugen.
kubectl apply -f backup-secret.yaml
```

Die zu setzenden Werte (im Abschnitt `[offsite]` der `rclone.conf`):

| Wert | Beschreibung |
|---|---|
| `host` | NAS-Host/IP (bei SFTP) bzw. `endpoint` (bei S3/MinIO) |
| `user` | Backup-Benutzer auf dem NAS |
| `pass` | mit `rclone obscure` verschleiertes Passwort (**oder** `key_file` für SSH-Key) |

Der Remote-Name `[offsite]` muss zum `RCLONE_REMOTE` im CronJob passen
(Default `offsite:dms-backup`). Zielverzeichnis auf dem NAS vorher anlegen und
beschreibbar machen.

Danach greift der CronJob automatisch. Ad-hoc-Lauf zum Testen:

```bash
kubectl -n dms create job --from=cronjob/dms-backup dms-backup-manuell
kubectl -n dms logs job/dms-backup-manuell -f
```

## 3. Überwachung

```bash
kubectl -n dms get cronjob dms-backup           # LAST SCHEDULE / SUSPEND
kubectl -n dms get jobs -l app=dms-backup        # COMPLETIONS 1/1 = ok
kubectl -n dms logs job/<job-name>               # Detaillog
```
Ein fehlgeschlagener Lauf bleibt als `Failed`-Job sichtbar (siehe
`failedJobsHistoryLimit`). Optional später: Alerting darauf.

## 4. Restore (Wiederherstellung)

Voraussetzung: `backup-secret.yaml` ist angewendet (rclone-Remote verfügbar),
`kubectl`- und `rclone`-Zugriff vorhanden.

### 4.1 Backup vom Offsite-Ziel holen und prüfen

```bash
# jüngsten Zeitstempel-Ordner ermitteln
TS=$(rclone --config deploy/k8s/backup-secret.yaml lsf --dirs-only offsite:dms-backup | sort | tail -1 | tr -d /)
# (alternativ direkt aus dem gemounteten NAS-Ordner kopieren)
rclone --config <conf> copy "offsite:dms-backup/$TS" "./restore/$TS"
cd "./restore/$TS" && sha256sum -c SHA256SUMS      # MUSS "OK" liefern
```

### 4.2 Datenbank zurückspielen

> Restore in eine **frische/leere** DB. Bei Bedarf vorher Backend/Worker
> herunterfahren, damit nichts währenddessen schreibt.

```bash
# Backend + Worker pausieren
kubectl -n dms scale deploy/backend deploy/worker --replicas=0

# Dump in den postgres-Pod streamen und einspielen
gunzip -c "./restore/$TS/db.sql.gz" | \
  kubectl -n dms exec -i deploy/postgres -- psql -U dms -d dms

# Backend + Worker wieder hochfahren
kubectl -n dms scale deploy/backend deploy/worker --replicas=1
```

Für ein „sauberes" Restore (bestehende Objekte verwerfen) die DB vorher neu
anlegen: `DROP DATABASE dms; CREATE DATABASE dms OWNER dms;` (nur wenn keine
aktive Verbindung besteht – Backend/Worker vorher auf 0 skalieren).

### 4.3 `/data` zurückspielen

`/data` liegt auf der RWO-PVC `dms-data` (Backend-Node). Das tar in den
Backend-Pod streamen und dort entpacken:

```bash
kubectl -n dms exec -i deploy/backend -- \
  tar xzf - -C /data < "./restore/$TS/data.tar.gz"
```
Das Archiv enthält die Ordner `originals/ archive/ thumbnails/` relativ zu
`/data` und legt sie am Zielort wieder an. Thumbnails sind zur Not regenerierbar,
`originals/` und `archive/` sind revisionssicher und **müssen** stimmen.

### 4.4 Verifikation

```bash
# Dokument-Zähler DB vs. Dateien plausibel?
kubectl -n dms exec deploy/postgres -- psql -U dms -d dms -tAc \
  "select count(*) from documents_document;"
kubectl -n dms exec deploy/backend -- sh -c 'ls /data/originals | wc -l'
# UI aufrufen: Dokumente sichtbar, Volltextsuche/Vorschau funktionieren.
```

## 5. Restore-Drill (Nachweis)

Ein Backup ist erst dann echt, wenn ein Restore **nachweislich** funktioniert.
`deploy/k8s/restore-drill.sh` spielt das jüngste Offsite-Backup in eine
**wegwerfbare** Postgres-Instanz + ein Temp-Verzeichnis ein und prüft die
Integrität – ohne die Produktion anzufassen.

```bash
# gegen das echte Offsite-Ziel (liest nur, schreibt nichts an Prod):
RCLONE_CONF=deploy/k8s/backup-secret.yaml ./deploy/k8s/restore-drill.sh
```

Das Skript: holt das jüngste Backup, prüft `SHA256SUMS`, startet einen
Wegwerf-Postgres (Docker/Podman), spielt `db.sql.gz` ein, zählt Tabellen/Zeilen,
entpackt `data.tar.gz` in ein Temp-Verzeichnis und listet die Datei-Anzahl.
Ergebnis = Nachweis. Der jeweils aktuelle Drill-Nachweis wird unten protokolliert.

### Drill-Protokoll

| Datum | Durchgeführt von | Backup-TS | DB-Restore | /data-Restore | Ergebnis |
|---|---|---|---|---|---|
| _offen_ | Platform/QA (Heimnetz) | — | — | — | Erst-Drill nach Deploy des CronJob + gesetztem `backup-secret.yaml` (siehe STOAA-331) |

> Der Erst-Drill braucht das reale Offsite-Ziel (gesetztes Secret) und läuft
> daher als Verifikationsschritt im Heimnetz, nicht im PR. Nach erfolgreichem
> Lauf hier Datum/Ergebnis eintragen.
