#!/usr/bin/env bash
# Prüft, dass die in base/kustomization.yaml referenzierten Image-newTags
# TATSÄCHLICH in der Registry liegen. Verhindert, dass `kubectl apply -k`
# auf ein Geister-Image zeigt (Ursache der ErrImagePull-Ausfälle 2026-07-05).
#
# Nutzung:
#   deploy/k8s/verify-image-tags.sh                 # prüft beide Tags
#   Exit 0 = alle Tags vorhanden, Exit 1 = mindestens ein Tag fehlt.
#
# Als Preflight vor manuellem Rollout:
#   deploy/k8s/verify-image-tags.sh && kubectl apply -k deploy/k8s
set -euo pipefail

REGISTRY="${REGISTRY:-registry.stoegerer-home.at}"
KUSTOMIZATION="$(dirname "$0")/base/kustomization.yaml"

# newTag je Image aus der kustomization lesen (Zeile 'newTag: "..."' direkt nach
# der jeweiligen 'name: <registry>/<image>'-Zeile).
tag_for() {
  local image="$1"
  awk -v img="${REGISTRY}/${image}" '
    $0 ~ "name: " img "$" { found=1; next }
    found && /newTag:/ {
      gsub(/[",]/, "", $2); print $2; exit
    }
  ' "$KUSTOMIZATION"
}

fail=0
for image in dms-backend dms-frontend; do
  tag="$(tag_for "$image")"
  if [ -z "$tag" ]; then
    echo "WARN: kein newTag für ${image} in ${KUSTOMIZATION} gefunden – übersprungen."
    continue
  fi
  code="$(curl -sk -o /dev/null -w '%{http_code}' \
    -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
    "https://${REGISTRY}/v2/${image}/manifests/${tag}")"
  if [ "$code" = "200" ]; then
    echo "OK:     ${image}:${tag} vorhanden."
  else
    echo "FEHLER: ${image}:${tag} NICHT in der Registry (HTTP ${code}) – apply -k würde ErrImagePull auslösen."
    fail=1
  fi
done

exit "$fail"
