#!/usr/bin/env bash
# Guard gegen rotierende Kubernetes-Secrets.
#
# POSTGRES_PASSWORD ist an die initialisierte postgres-data-PVC gekoppelt:
# Postgres übernimmt das Passwort nur beim ersten initdb. Wird danach ein neues
# Secret generiert, können Backend/Worker nicht mehr einloggen. Deshalb müssen
# dms-secrets und insbesondere POSTGRES_PASSWORD stabile Secret-Werte bleiben.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if grep -RInE '^[[:space:]]*secretGenerator:' deploy/k8s; then
  cat >&2 <<'EOF'
FEHLER: deploy/k8s darf keinen kustomize secretGenerator verwenden.

Grund:
  POSTGRES_PASSWORD muss stabil bleiben. Ein neu generiertes Secret kann vom
  bereits initialisierten postgres-data PVC abweichen und verursacht:
  "password authentication failed for user dms".

Erlaubte Wege:
  1. deploy/k8s/secret.yaml lokal/gitignored pflegen und kubectl apply -f nutzen.
  2. Ein echtes SealedSecret mit festem verschlüsseltem Wert committen.

Nicht erlaubt:
  - secretGenerator für dms-secrets
  - zufällige/rotierende POSTGRES_PASSWORD-Werte im Deploy
EOF
  exit 1
fi

echo "Stable-secret guard OK: kein secretGenerator unter deploy/k8s."
