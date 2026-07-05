# NFS Consume Overlay – CD Deployment Setup

**STOAA-450 Fix:** Dieses Dokument beschreibt, wie das consume-nfs-Overlay zuverlässig via CD deployt wird (kein H1 Drift mehr).

## Problem (gelöst)

Vor STOAA-450: Die CD-Pipeline (`deploy.yml:57`) renderte `kubectl kustomize deploy/k8s` (**Basis**), nie das `overlays/consume-nfs`. Der Permission-Fix aus STOAA-434 (`securityContext runAsUser/fsGroup=1000`) + NFS-Volume + `CONSUME_FOLDER_PATH` + `CONSUME_PER_USER` wurden bei jedem Release verworfen → H1 Overlay-Drift.

## Lösung

Die CD-Pipeline wurde umgestellt auf `kubectl kustomize deploy/k8s/overlays/consume-nfs` und injiziert die NFS-Konfiguration aus GitHub Secrets (kein Klartext im Repo).

---

## Voraussetzung: GitHub Secrets setzen

Die Pipeline benötigt zwei Secrets für die NFS-Konfiguration. Diese MÜSSEN vor dem ersten Deploy gesetzt werden.

### 1. GitHub Secrets anlegen

Navigiere zu: **Settings → Secrets and variables → Actions → Repository secrets**

Lege folgende Secrets an:

| Secret Name | Beispielwert | Beschreibung |
|-------------|--------------|--------------|
| `NFS_SERVER` | `192.168.1.101` | IP oder DNS-Name des NAS (Synology/TrueNAS/etc.) |
| `NFS_EXPORT_PATH` | `/volume1/dms-consume` | NFS-Export-Pfad auf dem NAS |

**Wichtig:** Die echte IP/Pfad NICHT ins Repo committen. Die Platzhalter in `worker-nfs-patch.yaml` (`NFS_SERVER_PLACEHOLDER`, `NFS_EXPORT_PATH_PLACEHOLDER`) bleiben im Repo; die Pipeline ersetzt sie zur Deploy-Zeit mit den Secret-Werten.

### 2. Beispiel: Secrets setzen via GitHub CLI

```bash
gh secret set NFS_SERVER -b "192.168.1.101" --repo stoegerer/dms
gh secret set NFS_EXPORT_PATH -b "/volume1/dms-consume" --repo stoegerer/dms
```

### 3. Secrets verifizieren

```bash
gh secret list --repo stoegerer/dms
# Erwartete Ausgabe:
# NFS_SERVER        Updated 2026-07-05
# NFS_EXPORT_PATH   Updated 2026-07-05
```

---

## Synology NAS Export-Konfiguration

**Owner-Aktion (vor End-to-End-Verifikation):** Der NFS-Export auf dem NAS MUSS mit `all_squash,anonuid=1000,anongid=1000` konfiguriert sein, damit die Worker-Pods (UID 1000) Dateien lesen, schreiben und verschieben können.

### Empfohlene Synology-Konfiguration

**Datei:** `/etc/exports` (oder via Synology DSM UI: **Control Panel → Shared Folder → Edit → NFS Permissions**)

```
/volume1/dms-consume  192.168.1.0/24(rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=1000)
```

**Parameter-Erklärung:**
- `rw` – Read/Write-Zugriff
- `sync` – Schreibvorgänge werden sofort auf Disk committed (keine Cache-Inkonsistenzen)
- `no_subtree_check` – Performance-Optimierung (sicher bei dedizierten Exports)
- `all_squash` – Alle Client-UIDs werden auf anon-UID gemappt (security best practice)
- `anonuid=1000,anongid=1000` – Squash-Target = UID/GID 1000 (passt zu Worker `securityContext`)

**Nach Änderungen:**
```bash
sudo exportfs -ra  # Export-Tabelle neu laden
sudo exportfs -v   # Aktive Exports verifizieren
```

### Pro-User-Attribution (STOAA-246/261)

Das Overlay setzt `CONSUME_PER_USER=true`. Die NAS-Freigabe MUSS pro-User-Unterordner haben:

```
/volume1/dms-consume/
├── alice/     → Scans werden Document.owner=alice zugeordnet
├── bob/       → Scans werden Document.owner=bob zugeordnet
└── .../
```

- Ordner-Namen matchen case-insensitiv gegen Django `User.username`
- Ordner ohne passenden User werden übersprungen + geloggt (nie owner-los aufgenommen)
- Siehe `SYNOLOGY-NFS-SETUP.md` für Details

---

## Pipeline-Verifikation

Nach dem ersten Deploy (Push nach `main`):

### 1. GitHub Actions Log prüfen

Navigiere zu: **Actions → build-and-deploy** (letzter Run)

Prüfe den Step **"Manifeste rendern, Tags ersetzen, anwenden"**:
- Zeile sollte `kubectl kustomize deploy/k8s/overlays/consume-nfs` zeigen (nicht `deploy/k8s`)
- Keine Fehler bei `sed` (Platzhalter erfolgreich ersetzt)
- `kubectl apply` erfolgreich (keine NFS-Mount-Fehler)

### 2. Cluster-Verifikation (NFS Overlay aktiv)

Auf dem k3s-Server:

```bash
# Verify securityContext (STOAA-434 Fix)
kubectl -n dms get deploy/worker -o yaml | grep -A3 securityContext
# Erwartete Ausgabe:
#   securityContext:
#     runAsUser: 1000
#     runAsGroup: 1000
#     fsGroup: 1000

# Verify NFS volume
kubectl -n dms get deploy/worker -o yaml | grep -A5 "name: consume-nfs"
# Erwartete Ausgabe:
#   - name: consume-nfs
#     nfs:
#       server: 192.168.1.101  # <-- echte IP, nicht PLACEHOLDER
#       path: /volume1/dms-consume
#       readOnly: false

# Verify CONSUME_FOLDER_PATH
kubectl -n dms exec deploy/worker -- printenv CONSUME_FOLDER_PATH
# Erwartete Ausgabe: /consume-nfs

# Verify CONSUME_PER_USER
kubectl -n dms exec deploy/worker -- printenv CONSUME_PER_USER
# Erwartete Ausgabe: true
```

### 3. Automatisierte Verifikation

```bash
cd deploy/k8s/overlays/consume-nfs
./verify-nfs-overlay.sh
```

**Erwartete Ausgabe:** `✓ All automated checks PASSED`

Wenn Fehler auftreten:
- Prüfe GitHub Secrets (NFS_SERVER, NFS_EXPORT_PATH gesetzt?)
- Prüfe Synology NFS Export (`exportfs -v`, UID 1000 mapping?)
- Prüfe Worker-Pod-Events: `kubectl -n dms describe pod -l app=worker`

---

## End-to-End-Test (nach Synology-Export-Setup)

**Owner-Aktion benötigt:** Synology-Export mit `anonuid=1000,anongid=1000` konfigurieren (siehe oben).

**Sobald NAS konfiguriert:**

1. Test-PDF in NAS-User-Ordner legen:
   ```bash
   # Beispiel: alice-User
   cp test.pdf /volume1/dms-consume/alice/
   ```

2. Worker-Mount verifizieren:
   ```bash
   kubectl -n dms exec deploy/worker -- ls -la /consume-nfs/alice/
   # Sollte test.pdf zeigen (owner 1000:1000)
   ```

3. Schreibrechte testen:
   ```bash
   kubectl -n dms exec deploy/worker -- touch /consume-nfs/alice/.write-test
   kubectl -n dms exec deploy/worker -- rm /consume-nfs/alice/.write-test
   # Kein Permission-Denied → OK
   ```

4. Consume-Task triggern:
   ```bash
   kubectl -n dms exec deploy/worker -- python manage.py scan_consume_folder
   ```

5. Verifikation:
   ```bash
   # Datei in _processed verschoben?
   kubectl -n dms exec deploy/worker -- ls /consume-nfs/alice/_processed/
   
   # Dokument in DMS angelegt (owner=alice)?
   kubectl -n dms exec deploy/backend -- python manage.py shell -c \
     "from documents.models import Document; print(Document.objects.filter(owner__username='alice').latest('id'))"
   ```

**Akzeptanz:** Scan erfolgreich, Dokument mit `owner=alice` angelegt, kein Permission-Fehler.

---

## Drift-Verifikation (nach 2. Deploy)

Um zu bestätigen, dass das Overlay **persistent** bleibt (kein Drift mehr):

1. Dummy-Commit nach `main` pushen (z. B. README-Änderung)
2. Warten bis CD-Deploy abgeschlossen
3. Re-run: `./verify-nfs-overlay.sh`
4. **Erwartung:** Alle Checks grün (securityContext, NFS-Volume, Env-Vars unverändert)

**Wenn Drift erkannt wird:**
- Pipeline-Log prüfen: `kubectl kustomize deploy/k8s/overlays/consume-nfs` oder `deploy/k8s`?
- Secrets noch gesetzt? `gh secret list`

---

## Rollback (falls nötig)

Falls NFS-Probleme auftreten und schnell zurück zur Basis gewechselt werden soll:

```bash
# In .github/workflows/deploy.yml:
# kubectl kustomize deploy/k8s/overlays/consume-nfs  →  deploy/k8s
# (Platzhalter-sed entfernen)

git commit -m "Rollback to base (no NFS overlay)"
git push origin main
```

**Wichtig:** Worker fällt dann zurück auf `/data/consume` (local PVC), kein NFS-Mount.

---

## Troubleshooting

| Symptom | Ursache | Fix |
|---------|---------|-----|
| Pipeline-Fehler: `NFS_SERVER: unbound variable` | GitHub Secrets fehlen | Secrets setzen (siehe oben) |
| Worker-Pod: `MountVolume.SetUp failed: mount failed` | NAS nicht erreichbar oder Export-Pfad falsch | Prüfe `NFS_SERVER` IP, `exportfs -v` auf NAS |
| Worker-Pod: `Permission denied` in `/consume-nfs` | Synology NFS Export ohne `anonuid=1000` | Export-Regel anpassen, `exportfs -ra` |
| `verify-nfs-overlay.sh`: `CONSUME_FOLDER_PATH=/data/consume` | Overlay-ConfigMap nicht angewandt | Pipeline-Log prüfen, `kubectl kustomize` Pfad korrekt? |
| Drift nach 2. Deploy (Overlay-Config weg) | Pipeline auf alte Basis zurückgefallen | `.github/workflows/deploy.yml` prüfen, Zeile 60 |

---

## Akzeptanzkriterien (STOAA-450)

✅ Nach CD-Deploy hat `worker`-Deployment:
- `securityContext.runAsUser=1000 / fsGroup=1000` (STOAA-434 Fix persistent)
- NFS-Volume-Mount auf `/consume-nfs` mit echter NAS-IP (nicht PLACEHOLDER)
- `CONSUME_FOLDER_PATH=/consume-nfs`
- `CONSUME_PER_USER=true`

✅ Nach 2. Deploy (Drift-Test): Alle Werte **unverändert** (Verifikationsskript grün)

✅ NAS-IP bleibt außerhalb des Repos (nur in GitHub Secrets)

✅ Owner-Aktion dokumentiert: Synology-Export `all_squash,anonuid=1000,anongid=1000` (Voraussetzung für End-to-End-Test)

---

## Nächste Schritte (nach STOAA-450 done)

1. **CTO (STOAA-433):** End-to-End-Verifikation am Live-System (sobald NAS-Export gesetzt)
2. **CEO:** Abnahme + STOAA-407 schließen
