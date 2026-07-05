#!/bin/bash
# STOAA-175 QA Verification Script
# Run this after NFS overlay activation to verify the consume folder setup

set -euo pipefail

echo "=== STOAA-175 NFS Overlay Verification ==="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ERRORS=0

# 1. Verify overlay applied
echo "1. Checking if worker deployment has NFS volume..."
if kubectl -n dms get deploy/worker -o json | jq -e '.spec.template.spec.volumes[] | select(.name=="consume-nfs")' > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} NFS volume 'consume-nfs' found in worker deployment"
else
    echo -e "${RED}✗${NC} NFS volume 'consume-nfs' NOT found - overlay not applied?"
    ((ERRORS++))
fi

# 2. Verify NFS mount in pod
echo ""
echo "2. Checking NFS mount inside worker pod..."
if kubectl -n dms exec deploy/worker -- df -h /consume-nfs 2>/dev/null | grep -q consume-nfs; then
    echo -e "${GREEN}✓${NC} /consume-nfs is mounted"
    kubectl -n dms exec deploy/worker -- df -h /consume-nfs | tail -1
else
    echo -e "${RED}✗${NC} /consume-nfs NOT mounted"
    echo "Pod events:"
    kubectl -n dms describe pod -l app=worker | grep -A5 "Events:" || true
    ((ERRORS++))
fi

# 3. Verify securityContext (STOAA-434 fix)
echo ""
echo "3. Checking securityContext (UID/GID alignment for NFS)..."
RUN_AS_USER=$(kubectl -n dms get deploy/worker -o jsonpath='{.spec.template.spec.securityContext.runAsUser}' 2>/dev/null || echo "")
RUN_AS_GROUP=$(kubectl -n dms get deploy/worker -o jsonpath='{.spec.template.spec.securityContext.runAsGroup}' 2>/dev/null || echo "")
FS_GROUP=$(kubectl -n dms get deploy/worker -o jsonpath='{.spec.template.spec.securityContext.fsGroup}' 2>/dev/null || echo "")

if [[ "$RUN_AS_USER" == "1000" && "$RUN_AS_GROUP" == "1000" && "$FS_GROUP" == "1000" ]]; then
    echo -e "${GREEN}✓${NC} securityContext configured: runAsUser=1000, runAsGroup=1000, fsGroup=1000"
    # Verify actual UID in pod
    ACTUAL_UID=$(kubectl -n dms exec deploy/worker -- id -u 2>/dev/null || echo "")
    if [[ "$ACTUAL_UID" == "1000" ]]; then
        echo -e "${GREEN}✓${NC} Worker process running as UID 1000"
    else
        echo -e "${RED}✗${NC} Worker process running as UID $ACTUAL_UID (expected 1000)"
        ((ERRORS++))
    fi
else
    echo -e "${YELLOW}⚠${NC} securityContext not configured or incomplete (runAsUser=$RUN_AS_USER, runAsGroup=$RUN_AS_GROUP, fsGroup=$FS_GROUP)"
    echo "   This may cause NFS permission issues with root_squash enabled"
    ((ERRORS++))
fi

# 4. Verify write access
echo ""
echo "4. Testing write access to NFS mount..."
if kubectl -n dms exec deploy/worker -- touch /consume-nfs/.verify-write-test 2>/dev/null; then
    kubectl -n dms exec deploy/worker -- rm /consume-nfs/.verify-write-test
    echo -e "${GREEN}✓${NC} Write access confirmed"
else
    echo -e "${RED}✗${NC} Write access FAILED - check NFS permissions (UID/GID, root-squash)"
    echo "   Current worker UID: $(kubectl -n dms exec deploy/worker -- id 2>/dev/null || echo 'unknown')"
    echo "   NFS mount permissions: $(kubectl -n dms exec deploy/worker -- ls -ld /consume-nfs 2>/dev/null || echo 'unknown')"
    ((ERRORS++))
fi

# 5. Verify CONSUME_FOLDER_PATH config
echo ""
echo "5. Checking CONSUME_FOLDER_PATH environment variable..."
CONSUME_PATH=$(kubectl -n dms exec deploy/worker -- printenv CONSUME_FOLDER_PATH 2>/dev/null || echo "")
if [[ "$CONSUME_PATH" == "/consume-nfs" ]]; then
    echo -e "${GREEN}✓${NC} CONSUME_FOLDER_PATH=/consume-nfs (overlay active)"
elif [[ "$CONSUME_PATH" == "/data/consume" ]]; then
    echo -e "${YELLOW}⚠${NC} CONSUME_FOLDER_PATH=/data/consume (base config - overlay configmap not applied?)"
    ((ERRORS++))
else
    echo -e "${RED}✗${NC} CONSUME_FOLDER_PATH='$CONSUME_PATH' (unexpected value)"
    ((ERRORS++))
fi

# 5b. Verify CONSUME_PER_USER config (pro-User-Attribution, STOAA-246/261)
echo ""
echo "5b. Checking CONSUME_PER_USER environment variable..."
PER_USER=$(kubectl -n dms exec deploy/worker -- printenv CONSUME_PER_USER 2>/dev/null || echo "")
if [[ "$PER_USER" == "true" ]]; then
    echo -e "${GREEN}✓${NC} CONSUME_PER_USER=true (pro-User-Attribution aktiv; /consume-nfs/<username>/ → Document.owner)"
else
    echo -e "${YELLOW}⚠${NC} CONSUME_PER_USER='$PER_USER' (erwartet 'true' – Overlay-configmap nicht angewandt? Scans würden owner=None aufgenommen)"
    ((ERRORS++))
fi

# 6. Verify CONSUME_MIN_AGE
echo ""
echo "6. Checking CONSUME_MIN_AGE..."
MIN_AGE=$(kubectl -n dms exec deploy/worker -- printenv CONSUME_MIN_AGE 2>/dev/null || echo "")
if [[ "$MIN_AGE" == "15" ]]; then
    echo -e "${GREEN}✓${NC} CONSUME_MIN_AGE=15 (correct from STOAA-174)"
else
    echo -e "${YELLOW}⚠${NC} CONSUME_MIN_AGE='$MIN_AGE' (expected 15)"
fi

# 7. Verify nfs-common on node
echo ""
echo "7. Checking nfs-common availability on worker node..."
NODE=$(kubectl -n dms get pod -l app=worker -o jsonpath='{.items[0].spec.nodeName}')
echo "   Worker node: $NODE"
# This check requires node SSH access - skip if not available
echo -e "${YELLOW}⚠${NC} Manual verification needed: SSH to $NODE and run 'dpkg -l | grep nfs-common'"

# 8. Test consume folder processing (if scanner data available)
echo ""
echo "8. Manual test: Consume folder processing"
echo "   a. Place a test PDF in the NAS share (verify it appears in /consume-nfs)"
echo "   b. Wait CONSUME_MIN_AGE seconds (15s)"
echo "   c. Trigger or wait for next beat: scan_consume_folder runs every 120s"
echo "   d. Verify file moved to /consume-nfs/_processed/ and document created in DMS"

# Summary
echo ""
echo "=== Verification Summary ==="
if [[ $ERRORS -eq 0 ]]; then
    echo -e "${GREEN}✓ All automated checks PASSED${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Complete manual test (step 7) with actual scanner data"
    echo "  2. If successful, update .github/workflows/deploy.yml to use overlay:"
    echo "     kubectl kustomize deploy/k8s/overlays/consume-nfs"
    echo "  3. Mark STOAA-175 done, create QA sign-off ticket"
else
    echo -e "${RED}✗ $ERRORS check(s) FAILED${NC}"
    echo ""
    echo "Troubleshooting:"
    echo "  - Check deploy/k8s/overlays/consume-nfs/worker-nfs-patch.yaml (NFS_SERVER_PLACEHOLDER replaced?)"
    echo "  - Verify securityContext is set (runAsUser/runAsGroup/fsGroup=1000) - STOAA-434 fix"
    echo "  - Verify NAS export permissions (node IP in allow-list, all_squash,anonuid=1000,anongid=1000)"
    echo "  - Recommended Synology export: /volume1/dms-consume  <subnet>(rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=1000)"
    echo "  - Check pod events: kubectl -n dms describe pod -l app=worker"
    echo "  - Worker logs: kubectl -n dms logs deploy/worker"
    exit 1
fi
