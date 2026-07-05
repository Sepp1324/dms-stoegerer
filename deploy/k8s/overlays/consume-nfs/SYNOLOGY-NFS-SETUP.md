# Synology NFS Setup for DMS Consume Folder

**Issue:** STOAA-434 - NFS Permission Fix for consume-move operations  
**Date:** 2026-07-05

## Problem

The worker pod needs to read, write, and move files on the Synology NFS share for the consume folder workflow:
1. Scanner writes PDFs to `/volume1/dms-consume/<username>/`
2. Worker reads files, processes them, and moves them to `_processed/` or `_failed/`

With Synology's default `root_squash`, the worker pod's root user is mapped to `nobody`, causing permission denied errors when creating directories or moving files.

## Solution: UID Alignment

**Strategy:** Run the worker pod as UID/GID 1000 and configure the NFS export to map all users to the same UID.

### 1. Worker Pod Configuration (STOAA-434 Fix)

The `deploy/k8s/overlays/consume-nfs/worker-nfs-patch.yaml` now includes:

```yaml
spec:
  template:
    spec:
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
```

This ensures the worker process runs as UID/GID 1000, and all files created by the pod are owned by UID 1000.

### 2. Synology NFS Export Configuration

**Recommended configuration for `/volume1/dms-consume`:**

```
/volume1/dms-consume  <k8s-cluster-subnet>(rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=1000)
```

**Parameter breakdown:**
- `<k8s-cluster-subnet>` - Replace with your cluster's IP range (e.g., `192.168.1.0/24` or specific node IPs)
- `rw` - Read-write access
- `sync` - Synchronous writes (safer, slightly slower)
- `no_subtree_check` - Performance optimization
- **`all_squash`** - Map ALL users (including root) to anonymous UID/GID
- **`anonuid=1000,anongid=1000`** - Anonymous user is UID/GID 1000

### 3. Synology DSM Configuration Steps

#### Via DSM Web Interface:

1. **Open Control Panel** → **Shared Folder**
2. Select the consume folder (e.g., `dms-consume`)
3. Click **Edit** → **NFS Permissions** tab
4. Add or edit the NFS rule:
   - **Hostname or IP:** `<k8s-node-IP>` or `<cluster-subnet>`
   - **Privilege:** Read/Write
   - **Squash:** Map all users to admin (or custom UID 1000)
   - **Security:** sys
   - **Enable asynchronous:** Unchecked (use sync)
   - **Allow connections from non-privileged ports:** Checked
   - **Allow users to access mounted subfolders:** Checked

#### Via SSH (Advanced):

```bash
# 1. SSH to Synology NAS as admin
ssh admin@nas-ip

# 2. Become root
sudo -i

# 3. Edit /etc/exports
vi /etc/exports

# 4. Add or update the export line:
/volume1/dms-consume  192.168.1.0/24(rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=1000)

# 5. Reload NFS exports
exportfs -ra

# 6. Verify
exportfs -v
```

### 4. Verify Ownership on NAS

Ensure the shared folder and subdirectories are owned by UID 1000:

```bash
# On Synology NAS via SSH
sudo chown -R 1000:1000 /volume1/dms-consume
sudo chmod -R 755 /volume1/dms-consume
```

For per-user folders:
```bash
sudo mkdir -p /volume1/dms-consume/sepp /volume1/dms-consume/admin
sudo chown -R 1000:1000 /volume1/dms-consume/*
```

## Verification

After applying the configuration:

1. **Run the verification script:**
   ```bash
   cd deploy/k8s/overlays/consume-nfs
   ./verify-nfs-overlay.sh
   ```

2. **Manual test from worker pod:**
   ```bash
   kubectl -n dms exec deploy/worker -- sh -c '
     id &&
     ls -ld /consume-nfs &&
     touch /consume-nfs/test.txt &&
     mkdir -p /consume-nfs/test-user/_processed &&
     rm -rf /consume-nfs/test* &&
     echo "NFS permissions OK"
   '
   ```

3. **Test actual consume workflow:**
   - Place a test PDF in `/volume1/dms-consume/<username>/test.pdf` via scanner or SMB
   - Wait 15 seconds (CONSUME_MIN_AGE)
   - Check worker logs: `kubectl -n dms logs deploy/worker -f`
   - Verify file moved to `_processed/` and document created in DMS

## Troubleshooting

### Permission Denied Errors

**Symptoms:**
```
OSError: [Errno 13] Permission denied: '/consume-nfs/sepp/_processed'
```

**Check:**
1. Worker UID: `kubectl -n dms exec deploy/worker -- id` → should show `uid=1000`
2. NFS mount ownership: `kubectl -n dms exec deploy/worker -- ls -ld /consume-nfs` → should show `drwxr-xr-x ... 1000 1000`
3. Synology export: `exportfs -v` on NAS → should show `all_squash,anonuid=1000,anongid=1000`

**Fix:**
- Ensure `worker-nfs-patch.yaml` has `securityContext` with UID 1000
- Ensure Synology export has `all_squash,anonuid=1000,anongid=1000`
- Re-apply overlay: `kubectl apply -k deploy/k8s/overlays/consume-nfs`
- Restart worker: `kubectl -n dms rollout restart deploy/worker`

### Files Owned by Wrong UID

If files are owned by a different UID (e.g., scanner writes as UID 1026):

**Option A:** Change scanner to write as UID 1000  
**Option B:** Update both pod and NFS export to use UID 1026 instead of 1000

### Mount Failures

**Symptoms:**
```
MountVolume.SetUp failed for volume "consume-nfs" : mount failed: exit status 32
```

**Check:**
1. NFS server accessibility: `kubectl -n dms exec deploy/worker -- ping <nas-ip>`
2. NFS port open: `kubectl -n dms exec deploy/worker -- nc -zv <nas-ip> 2049`
3. `nfs-common` installed on worker node: SSH to node → `dpkg -l | grep nfs-common`
4. Node IP allowed in Synology NFS permissions

**Fix:**
- Install `nfs-common` on all worker nodes: `sudo apt-get install -y nfs-common`
- Add worker node IPs to Synology NFS allow-list
- Check firewall rules between cluster and NAS

## Alternative Configurations

### No root_squash (Less Secure)

If you control the environment and trust root access:

```
/volume1/dms-consume  <subnet>(rw,sync,no_subtree_check,no_root_squash)
```

Then remove `securityContext` from `worker-nfs-patch.yaml` (pod runs as root, UID 0).

**Security risk:** Container root = NFS root = full access to NAS.

### Custom UID (Scanner-Driven)

If the scanner writes as a specific UID (e.g., 1026), align the pod to match:

```yaml
# worker-nfs-patch.yaml
securityContext:
  runAsUser: 1026
  runAsGroup: 1026
  fsGroup: 1026
```

```
# Synology export
/volume1/dms-consume  <subnet>(rw,sync,no_subtree_check,all_squash,anonuid=1026,anongid=1026)
```

## References

- **Issue:** STOAA-434 - Live-Diagnose Consume-Move am Synology-NFS
- **Diagnostic Document:** `STOAA-434-NFS-CONSUME-DIAGNOSIS.md`
- **Kubernetes securityContext:** https://kubernetes.io/docs/tasks/configure-pod-container/security-context/
- **Synology NFS:** DSM Control Panel → Shared Folder → NFS Permissions
- **NFS exports(5):** `man exports` for detailed NFS export options
