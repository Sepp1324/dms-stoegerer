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

# --- Key-Drift-Guard für dms-db-backup-secrets (P2) ------------------------
# Statisch (kein Cluster): Jeder Key, den ein Manifest per secretKeyRef aus
# dms-db-backup-secrets zieht, MUSS in secret.example.yaml dokumentiert sein –
# sonst driftet der CronJob gegen ein Secret, das den Key gar nicht anbietet
# (Rollout grün, Backup scheitert). Zusätzlich müssen die Pflicht-Keys existieren.
python3 - <<'PY'
import re
import sys
from pathlib import Path

EXPECTED = {"DJANGO_SECRET_KEY", "POSTGRES_PASSWORD"}
example = Path("deploy/k8s/secret.example.yaml").read_text(encoding="utf-8")

# Keys, die secret.example.yaml im dms-db-backup-secrets-Block definiert.
block = example.split("name: dms-db-backup-secrets", 1)
defined = set()
if len(block) == 2:
    rest = block[1].split("\n---", 1)[0]
    for line in rest.splitlines():
        m = re.match(r"\s{2}([A-Z0-9_]+):", line)
        if m:
            defined.add(m.group(1))

missing_example = EXPECTED - defined
if missing_example:
    print(f"FEHLER: secret.example.yaml fehlt dms-db-backup-secrets-Key(s): {sorted(missing_example)}", file=sys.stderr)
    sys.exit(1)

# Alle in Manifesten referenzierten Keys von dms-db-backup-secrets einsammeln
# (Flow ``{ name: dms-db-backup-secrets, key: X }`` UND mehrzeilige Form).
referenced = set()
for path in Path("deploy/k8s").rglob("*.yaml"):
    text = path.read_text(encoding="utf-8")
    # Flow-Form
    for m in re.finditer(r"name:\s*dms-db-backup-secrets\s*,\s*key:\s*([A-Z0-9_]+)", text):
        referenced.add(m.group(1))
    # Mehrzeilige Form: name: dms-db-backup-secrets \n ... key: X
    for m in re.finditer(r"name:\s*dms-db-backup-secrets\b[^\n]*\n(?:\s.*\n)*?\s*key:\s*([A-Z0-9_]+)", text):
        referenced.add(m.group(1))

undocumented = referenced - defined
if undocumented:
    print(f"FEHLER: Manifeste referenzieren dms-db-backup-secrets-Key(s), die secret.example.yaml NICHT anbietet: {sorted(undocumented)}", file=sys.stderr)
    sys.exit(1)

print(f"Backup-Secret-Key-Guard OK: referenziert={sorted(referenced) or '—'}, dokumentiert={sorted(defined)}.")
PY
