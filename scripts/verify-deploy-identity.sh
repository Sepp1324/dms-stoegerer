#!/usr/bin/env bash
# Guard: der Deploy darf NUR mit der namespacebeschränkten Deploy-Identität
# laufen (system:serviceaccount:dms:dms-deployer), nie mit cluster-admin.
#
# Ein reiner Pfad-Check auf KUBECONFIG genügt nicht: eine an einen anderen Ort
# kopierte Admin-Kubeconfig käme durch. Deshalb wird die ECHTE Identität
# serverseitig geprüft (kubectl auth whoami) und cluster-admin explizit abgelehnt.
#
# Genutzt von .github/workflows/deploy.yml und deploy-frontend.yml.
set -euo pipefail

EXPECTED="system:serviceaccount:dms:dms-deployer"

# 1) KUBECONFIG gesetzt und lesbar?
if [ -z "${KUBECONFIG:-}" ] || [ ! -r "${KUBECONFIG}" ]; then
  echo "::error::Actions-Variable DMS_KUBECONFIG nicht gesetzt oder Datei nicht lesbar."
  echo "Erwartet: Pfad zur 0600-SA-Kubeconfig (dms-deployer). Siehe docs/ci-cd.md §1."
  exit 1
fi

# 2) Schnelle Pfad-Heuristik: niemals die k3s-Admin-Kubeconfig.
case "${KUBECONFIG}" in
  /etc/rancher/k3s/*)
    echo "::error::KUBECONFIG zeigt auf die k3s-Admin-Kubeconfig – bitte die SA-Kubeconfig verwenden."
    exit 1;;
esac

# 3) ECHTE Identität serverseitig prüfen. kubectl auth whoami braucht k8s >= 1.26
#    (k3s erfüllt das). Leere Ausgabe -> fail-closed (nicht heimlich weiterlaufen).
who="$(kubectl auth whoami -o jsonpath='{.status.userInfo.username}' 2>/dev/null || true)"
if [ -z "${who}" ]; then
  echo "::error::Identität nicht feststellbar (kubectl auth whoami). kubectl >= 1.26 nötig; Kubeconfig/Token prüfen."
  exit 1
fi
if [ "${who}" != "${EXPECTED}" ]; then
  echo "::error::Deploy-Identität '${who}' != '${EXPECTED}'. Nur der namespacebeschränkte Deploy-SA ist erlaubt."
  exit 1
fi

# 4) Defense-in-depth: cluster-admin explizit ablehnen (falls der SA je
#    versehentlich cluster-weit gebunden würde). can-i exit 0 == erlaubt.
if kubectl auth can-i '*' '*' --all-namespaces >/dev/null 2>&1; then
  echo "::error::Deploy-Identität besitzt cluster-admin-Rechte ('*' '*' --all-namespaces) – abgelehnt."
  exit 1
fi

echo "Deploy-Identität ok: ${who} (Kontext $(kubectl config current-context 2>/dev/null || echo '?'))"
