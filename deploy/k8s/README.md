# DMS auf k3s – vollständige Konfigurationsanleitung

Diese Anleitung führt das DMS auf einem **k3s-Cluster** aus – vom leeren Server
bis zum erreichbaren `http://dms.stoegerer-home.cloud`. Optimiert für ein **Einzel-Node-Setup**
(Familien-Homelab); Hinweise für Multi-Node stehen jeweils dabei.

---

## 1. Was ausgerollt wird

| Objekt | Kind | Zweck |
|---|---|---|
| `dms` | Namespace | Alle Ressourcen leben hier |
| `dms-config` | ConfigMap | Nicht-geheime Einstellungen (Hosts, DB-Name, AI-Provider …) |
| `dms-secrets` | Secret | `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, API-Keys |
| `postgres` | Deployment + PVC (`postgres-data`, 5 Gi) + Service | Datenbank |
| `redis` | Deployment + Service | Celery-Broker |
| `backend` | Deployment (Init-Container: migrate + collectstatic) + Service `:8000` | Django/DRF |
| `worker` | Deployment | Celery-Worker (OCR-Pipeline + Consume-Scanner) |
| `frontend` | Deployment + Service `:80` | React-SPA (nginx) |
| `dms-data` | PVC (20 Gi) | Revisionssichere Ablage `/data` (originals + archive) |
| `consume-nfs` | NFS-Volume (**optionales Overlay**, extern konfigurierbar) | Scanner-Eingang direkt auf NAS |
| `dms` | Ingress (Traefik) | `/api`,`/admin`,`/static` → Backend, `/` → Frontend |

```
                 ┌──────────── Traefik Ingress (dms.stoegerer-home.cloud) ───────────┐
                 │  /api /admin /static → backend    / → frontend      │
                 └───────────────┬───────────────────────┬────────────┘
                          ┌──────▼──────┐          ┌───────▼───────┐
                          │  backend    │          │   frontend    │
                          │ (gunicorn)  │          │   (nginx)     │
                          └──┬───────┬──┘          └───────────────┘
                             │       │
                   ┌─────────▼─┐  ┌──▼────────┐        ┌──────────┐
                   │ postgres  │  │  redis    │◀───────│  worker  │
                   └───────────┘  └───────────┘        └──────────┘
                             │                              │
                             └──────── PVC dms-data (/data) ┘
```

---

## 2. Voraussetzungen

- Ein Linux-Server (Ubuntu/Debian o. ä.), ≥ 2 GB RAM, ≥ 20 GB frei.
- Root/sudo auf dem Server.
- `docker` **auf der Maschine, die die Images baut** (kann der Server selbst sein).
- Grundkenntnisse `kubectl`.

> k3s bringt vieles mit, was wir brauchen: **Traefik** (Ingress), den
> **local-path-Provisioner** (Standard-`StorageClass` für die PVCs) und
> **containerd** als Container-Runtime. Es ist kein zusätzlicher Ingress- oder
> Storage-Controller nötig.

---

## 3. k3s installieren

### Control-Plane-Node (Server)

```bash
curl -sfL https://get.k3s.io | sh -

# Status prüfen
sudo systemctl status k3s
sudo k3s kubectl get nodes
```

### (Optional) weitere Nodes hinzufügen

```bash
# Auf dem Server: Join-Token auslesen
sudo cat /var/lib/rancher/k3s/server/node-token

# Auf dem zusätzlichen Node:
curl -sfL https://get.k3s.io | K3S_URL=https://<server-ip>:6443 \
  K3S_TOKEN=<token> sh -
```

> ⚠️ **Multi-Node-Hinweis:** Die PVC `dms-data` ist `ReadWriteOnce` (RWO) und wird
> von **backend + worker gemeinsam** genutzt. Beide müssen dann auf denselben Node.
> Für Einzel-Node ist das automatisch erfüllt. Für echtes Multi-Node siehe §12.

---

## 4. kubectl einrichten

```bash
# Kubeconfig für den eigenen Nutzer verfügbar machen
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
# Bei Remote-Zugriff: server: https://127.0.0.1:6443 → auf die Server-IP ändern

kubectl get nodes        # sollte "Ready" zeigen
```

---

## 5. Images bauen und in die Registry pushen

Dieses Setup nutzt eine **Container-Registry** (`registry.stoegerer-home.at`).
Die Deployments referenzieren `registry.stoegerer-home.at/dms-*:latest` mit
`imagePullPolicy: Always`, sodass jeder Node beim Rollout die aktuelle Version
zieht. Kein Tar-Kopieren zwischen den Nodes mehr.

### 5a. Nodes für die Registry konfigurieren (einmalig)

Da die Registry self-signed / unsicher ist, muss containerd ihr auf **jedem
Node** vertrauen. Vorlage: [`registries.yaml.example`](registries.yaml.example).

```bash
sudo cp deploy/k8s/registries.yaml.example /etc/rancher/k3s/registries.yaml
# (Inhalt bei Bedarf anpassen: HTTPS-self-signed vs. HTTP)

# Node-Dienst neu starten, damit containerd die Konfig lädt:
sudo systemctl restart k3s          # Server-Node (k3s-01)
sudo systemctl restart k3s-agent    # auf jedem Agent-Node (k3s-02, k3s-03)
```

Die Datei muss auf allen Nodes liegen (per `scp` verteilen), sonst schlägt der
Pull auf den nicht-konfigurierten Nodes fehl.

### 5b. Bauen & pushen (bei jeder neuen Version)

**Immer mit Versions-Tag** – nie `:latest`. Die Version wird an einer Stelle
gepflegt: `newTag` in [`kustomization.yaml`](kustomization.yaml).

```bash
# Im Projekt-Root – Version einmal setzen und überall verwenden
VERSION=0.1.0

docker build -t registry.stoegerer-home.at/dms-backend:$VERSION  ./backend
docker build -t registry.stoegerer-home.at/dms-frontend:$VERSION ./frontend

docker push registry.stoegerer-home.at/dms-backend:$VERSION
docker push registry.stoegerer-home.at/dms-frontend:$VERSION
```

Dann in `kustomization.yaml` beide `newTag` auf `$VERSION` setzen (siehe §7).

> **`docker push` scheitert mit x509/Zertifikatsfehler?** Dann vertraut der
> **Docker-Daemon** der Registry noch nicht. In `/etc/docker/daemon.json` auf der
> Build-Maschine ergänzen: `{"insecure-registries": ["registry.stoegerer-home.at"]}`
> und `sudo systemctl restart docker`. (Betrifft nur den Build-Host, nicht die Nodes.)

> **Warum Versions-Tags statt `:latest`?** Nur so ist reproduzierbar, welche
> Version auf welchem Node läuft, und ein Rollback ist ein simpler Tag-Wechsel.

> **Hinweis Storage & Node-Zuordnung:** Bei einem **CSI-Provisioner** (z. B.
> Longhorn) werden PVC-Pods **nicht** automatisch ko-lokalisiert. Da `backend`
> und `worker` die RWO-PVC `dms-data` teilen, erzwingt `celery.yaml` per
> `podAffinity`, dass der Worker auf demselben Node wie das Backend läuft (siehe
> §12 für die RWX-Alternative). Die Registry muss von allen Nodes erreichbar sein.

---

## 6. Konfiguration anpassen

### 6a. Secret erstellen (Pflicht)

`secret.yaml` ist per `.gitignore` ausgeschlossen und wird aus der Vorlage erzeugt:

```bash
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml
```

In `deploy/k8s/secret.yaml` eintragen:

| Schlüssel | Wert |
|---|---|
| `DJANGO_SECRET_KEY` | langer Zufallswert – z. B. `python -c 'import secrets;print(secrets.token_urlsafe(50))'` |
| `POSTGRES_PASSWORD` | sicheres DB-Passwort |
| `ANTHROPIC_API_KEY` | dein Claude-API-Key (nur wenn `AI_PROVIDER=anthropic`) |
| `OPENAI_API_KEY` | optional |

### 6b. ConfigMap prüfen (`deploy/k8s/configmap.yaml`)

- `DJANGO_ALLOWED_HOSTS` / `DJANGO_CORS_ORIGINS` – müssen den Ingress-Host (`dms.stoegerer-home.cloud`) enthalten.
- `AI_PROVIDER` – `anthropic` (Default), `ollama` (lokal, ohne Cloud) oder `disabled`.
- `AI_MODEL` – Default `claude-opus-4-8`; für günstige Massen-Klassifizierung `claude-haiku-4-5`.
- Für **Ollama** (Datenschutz, lokal): `AI_PROVIDER=ollama` und `OLLAMA_BASE_URL`
  auf einen erreichbaren Ollama-Dienst zeigen lassen (im Cluster als eigener
  Service oder extern).

---

## 7. Ausrollen

```bash
# 1. Bootstrap zuerst (Admin): Namespace dms + Deploy-ServiceAccount/RBAC.
#    Das Secret lebt im Namespace und braucht ihn vorab.
kubectl apply -k deploy/k8s/bootstrap

# 2. Secret (steht bewusst nicht in der kustomization)
kubectl apply -f deploy/k8s/secret.yaml

# 3. Der Rest per kustomize (Namespace ist NICHT mehr in base – kommt aus Schritt 1)
kubectl apply -k deploy/k8s

# Hochlaufen beobachten
kubectl -n dms get pods -w
```

> ⚠️ **Reihenfolge wichtig:** Wird das Secret vor dem Namespace angewendet,
> kommt `namespaces "dms" not found`. Deshalb Namespace → Secret → Rest.

Ablauf: `postgres`/`redis` starten → Backend-**Init-Container** wartet auf die DB,
führt `migrate` und `collectstatic` aus → dann laufen `backend`, `worker`,
`frontend`. Alle Pods sollten `Running` / `Ready` werden.

---

## 8. Admin-Nutzer anlegen

```bash
kubectl -n dms exec -it deploy/backend -- python manage.py createsuperuser
```

Danach kannst du dich unter `http://dms.stoegerer-home.cloud/admin/` anmelden und dort
Korrespondenten, Dokumenttypen, Tags, Klassifizierungsregeln und Nutzer pflegen.

---

## 9. Zugriff einrichten (DNS)

Der Ingress hört auf den Host **`dms.stoegerer-home.cloud`**. Der DNS-Eintrag wird
zentral in der **UDM (UniFi)** gepflegt – ein A-Record auf eine Node-IP:

```bash
# Node-IP ermitteln (eine der drei genügt – Traefik liegt auf jeder Node-IP)
kubectl get nodes -o wide      # Spalte INTERNAL-IP
```

Dann in der UDM: `dms.stoegerer-home.cloud  A  <node-ip>` anlegen. Kein
`/etc/hosts`-Eintrag nötig – gilt für alle Geräte im Netz.

Aufruf im Browser: **http://dms.stoegerer-home.cloud** (SPA) ·
**http://dms.stoegerer-home.cloud/admin/** (Verwaltung).

> **Vor dem DNS-Eintrag testen** (umgeht DNS, spricht Traefik direkt an):
> ```bash
> curl -s -H 'Host: dms.stoegerer-home.cloud' http://127.0.0.1/api/health/
> ```
> Kommt `{"status":"ok",…}`, funktioniert der Ingress – dann fehlt nur noch DNS.

---

## 10. Verifizieren

```bash
# Alle Objekte im Namespace
kubectl -n dms get all,ingress,pvc

# Health-Check über den Ingress
curl http://dms.stoegerer-home.cloud/api/health/
# → {"status":"ok","service":"dms-backend","version":"0.1.0","database":"ok"}

# Logs
kubectl -n dms logs deploy/backend
kubectl -n dms logs deploy/worker      # Celery/OCR
```

**Ende-zu-Ende-Test** (Token holen, PDF hochladen):

```bash
TOKEN=$(curl -s http://dms.stoegerer-home.cloud/api/auth/token/ \
  -d 'username=<user>&password=<pass>' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')

curl -s http://dms.stoegerer-home.cloud/api/documents/upload/ \
  -H "Authorization: Bearer $TOKEN" \
  -F 'file=@rechnung.pdf' -F 'title=Stadtwerke Januar'
```

Der Worker verarbeitet OCR + Ablage; im Admin erscheinen Dokument, Version
(mit `sha256`/`prev_hash`) und Audit-Log.

---

## 11. Betrieb

### Neue Version ausrollen

Immer mit neuem Versions-Tag (z. B. `0.2.0`):

```bash
VERSION=0.2.0
docker build -t registry.stoegerer-home.at/dms-backend:$VERSION  ./backend
docker build -t registry.stoegerer-home.at/dms-frontend:$VERSION ./frontend
docker push registry.stoegerer-home.at/dms-backend:$VERSION
docker push registry.stoegerer-home.at/dms-frontend:$VERSION

# newTag in kustomization.yaml auf $VERSION setzen, dann:
kubectl apply -k deploy/k8s
kubectl -n dms rollout status deploy/backend
```

Der Tag-Wechsel in `kustomization.yaml` ändert die Pod-Spezifikation → das
Rollout startet automatisch (kein manuelles `rollout restart` nötig).
**Rollback:** `newTag` zurück auf die alte Version, `kubectl apply -k`.

### Consume-Ordner (Scanner-Eingang)

Der Worker scannt periodisch (Celery Beat, alle 120s, `CONSUME_SCAN_INTERVAL`)
einen Eingangsordner und schickt reife Dateien durch die OCR-Pipeline.

- **Basis (Default):** lokaler Pfad `CONSUME_FOLDER_PATH=/data/consume` im
  `dms-data`-PVC. Dateien z. B. per `kubectl cp` oder Sidecar hineinlegen. So
  deployt die CD ohne externe Abhängigkeit — **kein NFS-Server nötig**.
- **NAS-Betrieb (optional):** ein **separates NFS-Volume** (`/consume-nfs`), auf
  das ein Netzwerkscanner/MFC direkt schreibt. Aktivierung über das Overlay
  `deploy/k8s/overlays/consume-nfs/` (siehe unten).

> **Warum ein Overlay und nicht die Basis?** Das NFS-Volume trägt Platzhalter für
> Server/Export (kein Secret/keine IP ins Git). Läge es in der Basis, würde die CD
> (`deploy.yml` → `kubectl apply -k deploy/k8s` + `rollout status deploy/worker`)
> versuchen, gegen `NFS_SERVER_PLACEHOLDER` zu mounten → der Worker-Pod hinge in
> `ContainerCreating` und der Deploy liefe rot. Deshalb ist NFS bewusst opt-in.

#### NAS-Betrieb aktivieren

1. **NAS-Export anlegen** (z. B. Synology, TrueNAS):
   ```
   Pfad: /volume1/dms-consume
   Berechtigungen: root-squash deaktivieren ODER feste UID/GID (z. B. 1000:1000)
   Zugriff: Node-IP-Range (z. B. 192.168.1.0/24)
   ```

2. **Node vorbereiten** (auf allen Worker-Nodes):
   ```bash
   sudo apt-get install -y nfs-common      # Debian/Ubuntu
   # bzw. yum install nfs-utils            # RHEL/CentOS

   # Test-Mount (Server-IP + Pfad anpassen)
   sudo mount -t nfs 192.168.1.10:/volume1/dms-consume /mnt
   ls /mnt && sudo umount /mnt             # sollte ohne Fehler durchlaufen
   ```

3. **Platzhalter im Overlay setzen** — in
   `deploy/k8s/overlays/consume-nfs/worker-nfs-patch.yaml`:
   ```yaml
   server: NFS_SERVER_PLACEHOLDER    →  server: 192.168.1.10
   path: NFS_EXPORT_PATH_PLACEHOLDER →  path: /volume1/dms-consume
   ```
   Das Overlay ergänzt Volume + Mount am Worker, setzt `securityContext` (UID/GID
   Alignment für NFS-Berechtigungen, STOAA-434) und überschreibt
   `CONSUME_FOLDER_PATH` auf `/consume-nfs` (`configmap-nfs-patch.yaml`).
   Rendern zum Prüfen: `kubectl kustomize deploy/k8s/overlays/consume-nfs`.

   **Synology NFS:** Für Synology DiskStation siehe detaillierte Anleitung in
   [`overlays/consume-nfs/SYNOLOGY-NFS-SETUP.md`](overlays/consume-nfs/SYNOLOGY-NFS-SETUP.md)
   — erklärt `all_squash`, `anonuid=1000`, UID-Alignment und Troubleshooting.

4. **Rollout + Verifikation:**
   ```bash
   kubectl apply -k deploy/k8s/overlays/consume-nfs
   kubectl -n dms rollout status deploy/worker

   # Automatisierte Verifikation (inkl. UID-Check, STOAA-434)
   cd deploy/k8s/overlays/consume-nfs
   ./verify-nfs-overlay.sh

   # Oder manuell:
   kubectl -n dms exec deploy/worker -- df -h /consume-nfs
   kubectl -n dms exec deploy/worker -- id  # sollte uid=1000 zeigen
   kubectl -n dms exec deploy/worker -- touch /consume-nfs/test && \
     kubectl -n dms exec deploy/worker -- rm /consume-nfs/test
   # → Schreibzugriff OK, Datei sollte auch auf dem NAS sichtbar sein
   ```

5. **Dauerhaft in der CD** (damit der NFS-Mount Deploys übersteht): in
   `.github/workflows/deploy.yml` den Render-Pfad von `deploy/k8s` auf
   `deploy/k8s/overlays/consume-nfs` umstellen. Bis dahin deployt die CD die
   NFS-freie Basis; ein manuell angewandtes Overlay würde beim nächsten
   Basis-Deploy wieder überschrieben.

#### Scanner konfigurieren

Typisches Scan-to-Folder-Setup (Beispiel Brother MFC):
1. Scanner-Web-UI öffnen → **Scan to Network**
2. Ziel: `\\NAS-IP\dms-consume` (Windows) oder `smb://NAS-IP/dms-consume` (Linux)
3. Oder direkt NFS, falls das Gerät es unterstützt.

Die Dateien landen dann im Consume-Ordner; der Worker erkennt sie erst, wenn sie
mindestens `CONSUME_MIN_AGE` Sekunden alt sind (Default: 15s, siehe `configmap.yaml`),
damit langsam über NFS geschriebene Scans nicht als Teil-Read aufgenommen werden.

#### Automatische Verarbeitung

Der Beat-Schedule (siehe `configmap.yaml`) stößt `scan_consume_folder` alle 120s an.
Jede Datei im Consume-Ordner, die älter als `CONSUME_MIN_AGE` ist, wird:
1. OCR-verarbeitet (wie ein Upload)
2. Im DMS-Archiv abgelegt (`/data/archive/…`)
3. Nach `_processed/` verschoben (Idempotenz-Marker; Fehler → `_failed/`)

Manuelle Triggerung (z. B. für Tests):
```bash
kubectl -n dms exec deploy/worker -- \
  python -c "from documents.tasks import scan_consume_folder as s; print(s())"
```

#### Troubleshooting

| Problem | Lösung |
|---|---|
| Pod `ContainerCreating` hängt, Event `MountVolume.SetUp failed for volume "consume-nfs"` | `nfs-common` fehlt auf dem Node → §2 |
| `mount.nfs: access denied` | NAS-Export-Berechtigungen prüfen (Node-IP in Allow-List? root-squash?) |
| Mount OK, aber `Permission denied` beim Schreiben | **STOAA-434:** UID/GID-Mismatch. Pod `id` sollte 1000 sein, NFS Export braucht `all_squash,anonuid=1000,anongid=1000`. Siehe [`SYNOLOGY-NFS-SETUP.md`](overlays/consume-nfs/SYNOLOGY-NFS-SETUP.md) |
| `OSError: [Errno 13] Permission denied: '/consume-nfs/<user>/_processed'` | **STOAA-434:** Verzeichnisse nicht beschreibbar. Auf NAS: `chown -R 1000:1000 /volume1/dms-consume` + NFS Export auf `anonuid=1000` setzen |
| Dateien werden nicht verarbeitet | Worker-Logs prüfen; `CONSUME_MIN_AGE` zu hoch? Datei-Timestamp korrekt? |
| Alte Scans akkumulieren | Beat läuft nicht → `kubectl -n dms logs deploy/beat`, Schedule-Konfig prüfen |

**Wichtig:** Das NFS-Volume wird **nur** vom Worker genutzt. Backend und Beat
greifen **nicht** darauf zu (kein Mount in deren Deployments). Die `dms-data`-PVC
(RWO, per `podAffinity` ko-lokalisiert) bleibt unverändert für Originale + Archiv.

### Skalierung

`backend` und `frontend` sind zustandslos und horizontal skalierbar
(`kubectl -n dms scale deploy/backend --replicas=2`) – **solange** alle Backend-
und Worker-Pods auf dem Node mit der `dms-data`-PVC liegen (RWO). Für echte
Verteilung siehe §12.

---

## 12. Multi-Node & geteilter Speicher

Die `dms-data`-PVC ist `ReadWriteOnce` und wird von `backend` **und** `worker`
genutzt. Bei einem CSI-Provisioner scattern die Pods sonst über Nodes und der
zweite Mount scheitert (`FailedMount … volumeattachments … no relationship`).
Zwei Wege:

1. **Umgesetzt (Default):** `worker` hat eine `podAffinity` auf `app: backend`
   (in `celery.yaml`) → beide laufen auf demselben Node, das RWO-Volume ist nur
   dort angehängt. Einfach, aber keine echte Verteilung.
2. **Sauber (RWX):** ein **RWX-fähiges** Storage nutzen – z. B.
   [Longhorn](https://longhorn.io) (RWX über NFS) oder ein NFS-Provisioner. Dann
   in `backend.yaml` die `dms-data`-PVC auf `accessModes: ["ReadWriteMany"]` und
   die passende `storageClassName` setzen und die `podAffinity` in `celery.yaml`
   entfernen. Backend ist dann über mehrere Nodes skalierbar.

---

## 13. Backup & Restore

Gesichert werden **zwei** zusammengehörige Dinge: die **Datenbank** (Metadaten,
Hash-Kette, Audit-Trail) und **`/data`** (`archive/`, `thumbnails/`, `consume/`).

Das läuft automatisiert als k8s-**CronJob** `backup` (`backup-cronjob.yaml`),
täglich 02:00, mit Offsite-Ablage auf das NAS via **SSH/rsync** und Retention.

**Einmalige Einrichtung (Owner):** SSH-Zugang zum NAS in das gitignored Secret
`dms-backup-secrets` setzen (Vorlage `secret.example.yaml`, zweiter Block):

```bash
cd deploy/k8s
cp secret.example.yaml secret.yaml   # BACKUP_SSH_* + BACKUP_TARGET_PATH füllen
kubectl apply -f secret.yaml
```

Manueller Ad-hoc-Lauf: `kubectl -n dms create job --from=cronjob/backup backup-manual`.

Backup-Monitoring:

- Der Backup-Job schreibt Start/Erfolg/Fehler in die DMS-Datenbank
  (`BackupMonitor`, sichtbar im Django-Admin).
- Der Restore-Drill schreibt seinen letzten Status ebenfalls dorthin.
- Die Admin-UI zeigt den Systemstatus unter **System** an und warnt, wenn das
  letzte erfolgreiche Backup älter als `BACKUP_ALERT_AFTER_HOURS` ist
  (Default: 36).

> **Vollständige Doku – Architektur, Restore-Schritte und Restore-Drill:**
> siehe [`docs/backup.md`](../../docs/backup.md). Ein Backup gilt erst als echt,
> wenn ein Restore einmal nachweislich durchgespielt wurde
> (`deploy/k8s/restore-drill.sh`).

---

## 14. TLS/HTTPS (interne CA – empfohlen fürs LAN)

Ohne TLS gehen Login & JWT **unverschlüsselt** durchs LAN. Der Ingress hört
bereits auf `web` **und** `websecure` und referenziert das Secret `dms-tls`
(`deploy/k8s/base/ingress.yaml`). Zertifikat + Redirect kommen aus
`deploy/k8s/tls/` (interne Cluster-CA via cert-manager – LAN-tauglich, keine
externe DNS-API nötig):

```bash
# 1) cert-manager installieren (einmalig, als Admin)
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
kubectl -n cert-manager rollout status deploy/cert-manager-webhook

# 2) Interne CA + Leaf-Zertifikat (dms-tls) + HTTP->HTTPS-Redirect anwenden
kubectl apply -k deploy/k8s/tls

# 3) Ingress rollen (falls noch nicht) – nutzt jetzt dms-tls
kubectl apply -k deploy/k8s/bootstrap   # Namespace/SA (falls neu)
kubectl apply -k deploy/k8s

# 4) Root-CA exportieren und auf den Familien-Geräten als vertrauenswürdig
#    importieren (dann keine Browser-Warnung):
kubectl -n cert-manager get secret dms-ca-root \
  -o jsonpath='{.data.tls\.crt}' | base64 -d > dms-ca.crt
```

Danach leitet `http://dms.stoegerer-home.cloud` automatisch auf `https://…` um. Der
CD-Deploy fasst `deploy/k8s/tls` **nicht** an (Admin-/One-time-Ressourcen, u. a.
im `kube-system`/`cert-manager`-Namespace – außerhalb der SA-Rechte).

> Alternative öffentlich-vertraute Zertifikate: statt der internen CA einen
> Let's-Encrypt-`ClusterIssuer` (DNS-01) verwenden und in `certificate.yaml` den
> `issuerRef` darauf zeigen lassen (braucht ein DNS-Provider-API-Token als Secret).

### 14a. Domain wechseln (Checkliste)

> ⚠️ **Wichtig:** Ein reiner Merge, der nur den Host in `configmap.yaml`/
> `ingress.yaml` ändert, rollt das **Zertifikat NICHT mit** – der CD-Deploy fasst
> `deploy/k8s/tls` bewusst nicht an (Admin-/One-time-Ressourcen außerhalb der
> SA-Rechte). Das Secret `dms-tls` trägt dann weiter das **alte** Zertifikat, und
> Traefik quittiert `https://<neue-domain>` mit **`tlsv1 alert internal error`**
> (Seite „nicht erreichbar", obwohl DNS + `:80`-Redirect funktionieren). Nach jeder
> Hoständerung daher als Cluster-Admin:

```bash
# 1) neuen Host in configmap.yaml (DJANGO_ALLOWED_HOSTS/CORS), ingress.yaml
#    (Host + TLS-Host) und tls/certificate.yaml (dnsNames) setzen, mergen lassen.

# 2) DNS-A-Record <neue-domain> -> Node-IP setzen (UDM).

# 3) Zertifikat für den neuen Host neu ausstellen (interne CA -> sofort):
kubectl apply -k deploy/k8s/tls
kubectl -n dms get certificate dms-tls -o wide     # READY=True + neuer dnsName?
# hängt es? Secret wegwerfen, cert-manager erzeugt es aus der Certificate neu:
kubectl -n dms delete secret dms-tls

# 4) Backend neu starten – laufende Pods haben DJANGO_ALLOWED_HOSTS/CORS noch aus
#    der ALTEN ConfigMap geladen (ein ConfigMap-Update restartet Pods NICHT):
kubectl -n dms rollout restart deploy/backend

# 5) Prüfen:
curl -k https://<neue-domain>/api/health/         # erwartet HTTP 200
```

---

## 15. Troubleshooting

| Symptom | Ursache / Lösung |
|---|---|
| Pod `ImagePullBackOff` | Image nicht in containerd importiert → §5 erneut; auf Multi-Node auf **jedem** Node importieren oder Registry nutzen |
| Backend `CrashLoopBackOff` | DB nicht erreichbar oder `dms-secrets` fehlt → `kubectl -n dms logs`, Secret angewendet? `kubectl -n dms get secret dms-secrets` |
| Init-Container hängt | Postgres noch nicht `Ready` → `kubectl -n dms get pods`, DB-Logs prüfen |
| `/admin/` ohne Styles | `collectstatic` lief nicht → Init-Container-Logs; Ingress-Pfad `/static` vorhanden? |
| `400 Bad Request`/`DisallowedHost` | Host nicht in `DJANGO_ALLOWED_HOSTS` (ConfigMap) → ergänzen, Rollout-Restart |
| Upload OK, aber kein OCR-Ergebnis | Worker-Logs (`kubectl -n dms logs deploy/worker`); Redis erreichbar? OCR-Binaries im Image (§ Dockerfile) |
| `dms.stoegerer-home.cloud` nicht erreichbar | DNS-A-Record (UDM) auf Node-IP gesetzt? Erst per `curl -H 'Host: …' http://127.0.0.1/api/health/` prüfen, ob Traefik antwortet; Traefik läuft (`kubectl -n kube-system get pods`)? |
| `404 page not found` (Traefik) beim Aufruf | Kein Router für den Host → Ingress vorhanden (`kubectl -n dms get ingress`)? `ingressClassName: traefik` gesetzt? Host im Ingress == angefragter Host? |
| `https://…` bricht mit `tlsv1 alert internal error` (`:80`-Redirect geht aber) | `dms-tls` trägt ein Zertifikat für den **alten** Host – nach Domainwechsel wurde `deploy/k8s/tls` nicht neu angewendet (CD rollt es nicht). → **§14a Domain wechseln**: `kubectl apply -k deploy/k8s/tls`, Cert `READY` prüfen, ggf. `kubectl -n dms delete secret dms-tls` |
| `https://…` „no available server" (Traefik) | Backend hat keine bereiten Endpoints → `kubectl -n dms get pods`/`endpoints backend`; meist laufender Rollout oder Readiness rot (`kubectl -n dms logs deploy/backend`) |
| PVC `Pending` | local-path-Provisioner aktiv? `kubectl get sc` sollte `local-path (default)` zeigen |
| postgres `Error`: `initdb: directory … not empty (lost+found)` | PVC liegt auf eigenem Dateisystem-Mount → bereits gelöst per `PGDATA=/var/lib/postgresql/data/pgdata` im `postgres.yaml` (Daten im Unterverzeichnis statt im Mount-Punkt) |
| `FailedMount … volumeattachments … no relationship found between node …` | RWO-PVC `dms-data` von backend+worker auf verschiedenen Nodes angefordert → durch `podAffinity` in `celery.yaml` gelöst (Co-Location). Dauerhaft besser: RWX-Storage, siehe §12 |

Nützlich:

```bash
kubectl -n dms describe pod <pod>     # Events unten ansehen
kubectl -n dms get events --sort-by=.lastTimestamp
```

---

## Schnell-Referenz (alles auf einmal)

```bash
# 1. k3s
curl -sfL https://get.k3s.io | sh -
mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config \
  && sudo chown $(id -u):$(id -g) ~/.kube/config

# 2. Registry auf jedem Node vertrauen + Images (mit Versions-Tag) pushen
sudo cp deploy/k8s/registries.yaml.example /etc/rancher/k3s/registries.yaml   # auf allen Nodes
sudo systemctl restart k3s          # bzw. k3s-agent auf den Agent-Nodes
VERSION=0.1.0   # muss dem newTag in kustomization.yaml entsprechen
docker build -t registry.stoegerer-home.at/dms-backend:$VERSION  ./backend
docker build -t registry.stoegerer-home.at/dms-frontend:$VERSION ./frontend
docker push registry.stoegerer-home.at/dms-backend:$VERSION
docker push registry.stoegerer-home.at/dms-frontend:$VERSION

# 3. Bootstrap → Secret → Deploy (Reihenfolge beachten!)
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml   # Werte eintragen!
kubectl apply -k deploy/k8s/bootstrap
kubectl apply -f deploy/k8s/secret.yaml
kubectl apply -k deploy/k8s

# 4. Superuser + Zugriff
kubectl -n dms exec -it deploy/backend -- python manage.py createsuperuser
# DNS-A-Record in der UDM: dms.stoegerer-home.cloud -> <node-ip>
curl -H 'Host: dms.stoegerer-home.cloud' http://127.0.0.1/api/health/   # Ingress-Test ohne DNS
curl http://dms.stoegerer-home.cloud/api/health/                        # nach DNS-Eintrag
```
