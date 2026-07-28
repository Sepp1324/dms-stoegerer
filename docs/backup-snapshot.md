# Konsistentes Backup per Longhorn/CSI-Snapshot (P1)

## Problem

Das bisherige Backup (`deploy/k8s/base/backup-cronjob.yaml`) dumpt zuerst
PostgreSQL und tart danach das **laufend veränderbare** `/data`. Wird zwischen
Dump und tar ein Dokument gelöscht (und dessen Datei-Cleanup ausgeführt), enthält
der Dump die Version, das TAR aber nicht mehr die Datei → **hängende Referenz**.
Ein bloßes Umsortieren von Dump/TAR verschiebt das Fenster nur (und trifft bei
Uploads sogar häufiger).

Die konsistente Lösung: einen **near-instant Longhorn/CSI-Snapshot** von `/data`
ziehen und aus einem daraus restaurierten, **unveränderlichen** Volume tarnen.
Der Snapshot friert `/data` in einem Punkt ein; `pg_dump` ist ohnehin
transaktional konsistent. Das minutenlange „live-tar"-Fenster verschwindet.

## Was dieses Repo bereits mitbringt (Admin-Bootstrap)

Beide Objekte sind **cluster-scoped bzw. rechtevergebend** und werden vom Admin
angewandt (`kubectl apply -k deploy/k8s/bootstrap`), NICHT von der CD:

- `deploy/k8s/bootstrap/volumesnapshotclass.yaml` – `VolumeSnapshotClass`
  `dms-longhorn-snapshot` (`driver.longhorn.io`, `type: snap`).
- `deploy/k8s/bootstrap/backup-snapshot-rbac.yaml` – ServiceAccount `dms-backup`
  + Role/RoleBinding (create/delete auf `volumesnapshots`, `persistentvolumeclaims`,
  `jobs`; get/watch auf `pods`).

## Voraussetzungen ZUERST prüfen (cluster-spezifisch)

Vor der Aktivierung verifizieren – sonst schlägt der Job zur Laufzeit fehl:

```bash
# 1) CSI-Snapshot-Controller + CRDs vorhanden?
kubectl get crd volumesnapshots.snapshot.storage.k8s.io
kubectl get pods -A | grep -i snapshot-controller

# 2) Longhorn-Treibername stimmt (driver.longhorn.io)?
kubectl get csidrivers

# 3) Ein Testsnapshot des Daten-PVC wird "readyToUse"?
kubectl -n dms apply -f - <<'EOF'
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata: { name: dms-data-testsnap, namespace: dms }
spec:
  volumeSnapshotClassName: dms-longhorn-snapshot
  source: { persistentVolumeClaimName: dms-data }
EOF
kubectl -n dms get volumesnapshot dms-data-testsnap -w   # readyToUse=true abwarten
kubectl -n dms delete volumesnapshot dms-data-testsnap

# 4) Ein kubectl-fähiges Image für den Orchestrator-Container festlegen
#    (das dms-backend-Image enthält i. d. R. KEIN kubectl). Entweder ein
#    kubectl-Image in die eigene Registry spiegeln oder dem Backend-Image
#    kubectl beilegen. Den Namen unten bei ORCHESTRATOR_IMAGE eintragen.
```

## Ablauf des Snapshot-Backups

Der Orchestrator-Pod (SA `dms-backup`, kubectl):

1. SSH-Vorabprüfung zur NAS (fail-fast wie bisher).
2. `VolumeSnapshot dms-data-snap-<ts>` von PVC `dms-data` anlegen, auf
   `readyToUse` warten.
3. Temp-PVC `dms-data-restore-<ts>` aus dem Snapshot ableiten
   (`dataSource: VolumeSnapshot`, `storageClassName: longhorn`, `accessMode: ROX/RWO`).
4. Helfer-`Job` starten, der das **restaurierte** Volume unter `/data` (readOnly)
   mountet und die eigentliche Sicherung macht: `pg_dump` (konsistent),
   `tar /data`, `scp DB+TAR` zur NAS, Rotation, `record_backup_status`.
   Der Helfer nutzt dieselbe `set -e` + Temp-Dump-dann-gzip-Logik wie
   `backup-cronjob.yaml` (kein `pg_dump | gzip`).
5. Auf den Helfer warten; dessen Exit-Code propagieren.
6. **Immer** aufräumen: Helfer-Job, Temp-PVC, Snapshot löschen.

## CronJob-Vorlage (erst nach Test aktivieren)

Diese Vorlage bewusst als Doku (nicht als auto-applied Manifest), bis sie
**einmal am Cluster getestet** wurde – ein ungetestetes P0-Backup darf das
funktionierende `backup-cronjob.yaml` nicht ablösen. `ORCHESTRATOR_IMAGE` und die
`storageClassName`/`accessModes` an die reale Umgebung anpassen, dann als
`deploy/k8s/base/backup-snapshot-cronjob.yaml` ablegen (mit `suspend: true`),
in `base/kustomization.yaml` aufnehmen, ausrollen und mit
`kubectl -n dms create job --from=cronjob/backup-snapshot backup-snap-test`
einen manuellen Lauf testen. Erst wenn Dump+TAR konsistent auf der NAS liegen,
`suspend` entfernen und das alte `backup-cronjob.yaml` außer Dienst nehmen.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata: { name: backup-snapshot, namespace: dms }
spec:
  schedule: "0 2 * * *"
  suspend: true            # ← erst nach erfolgreichem Testlauf entfernen
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 600
  jobTemplate:
    spec:
      activeDeadlineSeconds: 3600
      backoffLimit: 2
      template:
        spec:
          serviceAccountName: dms-backup
          restartPolicy: OnFailure
          containers:
            - name: orchestrator
              image: ORCHESTRATOR_IMAGE   # kubectl-fähiges Image (s. o.)
              envFrom:
                - secretRef: { name: dms-backup-secrets }
              command: ["/bin/sh","-c"]
              args:
                - |
                  set -e
                  TS=$(date +%Y%m%d-%H%M%S)
                  SNAP="dms-data-snap-${TS}"
                  PVC="dms-data-restore-${TS}"
                  JOB="backup-snap-tar-${TS}"
                  cleanup() {
                    kubectl -n dms delete job "$JOB" --ignore-not-found
                    kubectl -n dms delete pvc "$PVC" --ignore-not-found
                    kubectl -n dms delete volumesnapshot "$SNAP" --ignore-not-found
                  }
                  trap cleanup EXIT

                  # 1) Snapshot + warten
                  kubectl -n dms apply -f - <<EOF
                  apiVersion: snapshot.storage.k8s.io/v1
                  kind: VolumeSnapshot
                  metadata: { name: ${SNAP} }
                  spec:
                    volumeSnapshotClassName: dms-longhorn-snapshot
                    source: { persistentVolumeClaimName: dms-data }
                  EOF
                  kubectl -n dms wait --for=jsonpath='{.status.readyToUse}'=true \
                    volumesnapshot/${SNAP} --timeout=600s

                  # 2) Restore-PVC aus dem Snapshot
                  kubectl -n dms apply -f - <<EOF
                  apiVersion: v1
                  kind: PersistentVolumeClaim
                  metadata: { name: ${PVC} }
                  spec:
                    storageClassName: longhorn
                    dataSource:
                      name: ${SNAP}
                      kind: VolumeSnapshot
                      apiGroup: snapshot.storage.k8s.io
                    accessModes: ["ReadWriteOnce"]
                    resources: { requests: { storage: 20Gi } }
                  EOF

                  # 3) Helfer-Job: tart aus dem KONSISTENTEN Restore-Volume + shipt.
                  #    (Skript = backup-cronjob.yaml-Logik, /data = Restore-PVC RO.)
                  #    Muss der Admission-Guard (backup-snapshot-admission-policy.yaml)
                  #    erfüllen: eigene token-lose SA, nur erlaubte Secrets, Restore-PVC,
                  #    gepinntes dms-backend-Image, kein Privesc.
                  kubectl -n dms apply -f - <<EOF
                  apiVersion: batch/v1
                  kind: Job
                  metadata: { name: ${JOB} }
                  spec:
                    backoffLimit: 1
                    activeDeadlineSeconds: 3000
                    template:
                      spec:
                        serviceAccountName: dms-backup-helper   # eigene SA OHNE API-Rechte
                        automountServiceAccountToken: false      # kein Token im Helfer-Pod
                        restartPolicy: Never
                        containers:
                          - name: tar
                            image: registry.stoegerer-home.at/dms-backend:REPLACE
                            securityContext:
                              privileged: false
                              allowPrivilegeEscalation: false
                              capabilities: { drop: ["ALL"] }
                            envFrom:
                              - configMapRef: { name: dms-config }
                              - secretRef: { name: dms-secrets }
                              - secretRef: { name: dms-backup-secrets }
                            command: ["/bin/sh","-c"]
                            args: ["<backup-Skript: pg_dump->temp->gzip, tar /data, scp, rotate, record_backup_status>"]
                            volumeMounts:
                              - { name: restore, mountPath: /data, readOnly: true }
                            resources:
                              requests: { ephemeral-storage: "2Gi" }
                              limits: { ephemeral-storage: "20Gi" }
                        volumes:
                          - name: restore
                            persistentVolumeClaim: { claimName: ${PVC} }
                  EOF

                  # 4) Auf den Helfer warten + Ergebnis übernehmen
                  kubectl -n dms wait --for=condition=complete --timeout=3000s job/${JOB} \
                    || { kubectl -n dms logs job/${JOB} || true; exit 1; }
```

## Warum nicht im Orchestrator-Pod direkt tarnen?

Ein Pod kann kein **nachträglich** erzeugtes PVC mounten – Volumes stehen bei
Pod-Erstellung fest. Deshalb der Helfer-Job, der NACH dem Restore-PVC erzeugt
wird und es mountet.

## Alternative: Longhorn-native RecurringJob

Statt der kubectl-Orchestrierung kann Longhorn Snapshots + Versand selbst
übernehmen (`RecurringJob` + Backup-Target). Das braucht ein Longhorn-Backup-Target
(NFS/S3 auf der NAS) statt des heutigen SSH/scp – eine eigene Entscheidung.
```
