#!/usr/bin/env python3
"""Alte Image-Tags aus der Container-Registry entfernen (Standard: Trockenlauf).

Hintergrund: Jeder Deploy pusht dms-backend/-frontend unter dem Git-SHA. Die
Registry wurde nie aufgeräumt (>370 Tags je Repo, ~26 GB allein im Backend) –
ist ihr Speicher voll, scheitert `docker push` mit "blob upload invalid" bzw.
"unknown error" und der Deploy bleibt stehen.

Was das Skript tut
  1. Liest je Repo alle Tags samt Erstellungsdatum (nur GET/HEAD).
  2. Bestimmt die Tags, die BLEIBEN:
       - die neuesten N SHA-Tags (--keep-last, Default 10)
       - alle Nicht-SHA-Tags (latest, 0.1.0, …), außer mit --prune-named
       - Tags, die in den Manifesten unter deploy/k8s referenziert sind
       - Tags, die im Cluster gerade laufen (kubectl, Pods + CronJobs)
       - ausdrücklich genannte (--keep TAG, mehrfach)
  3. Zeigt, was gelöscht würde. Erst mit --apply wird gelöscht.

Sicherheiten
  - Ein Manifest wird nie gelöscht, wenn ein Tag, der bleibt, auf denselben
    Digest zeigt (Löschen per Digest entfernt sonst ALLE Tags darauf).
  - Die Images sind OCI-Indexe (Plattform-Manifest + Attestation). Das Skript
    löscht die Kind-Manifeste der entfernten Tags explizit mit. Dadurch reicht
    danach ein normaler Garbage-Collect; `--delete-untagged` ist NICHT nötig
    (und in registry 2.x für Index-Images riskant: es kann Kind-Manifeste von
    Tags entfernen, die bleiben sollen).
  - "Neueste" richtet sich nach dem Build-Datum im Image. Bei einem Build komplett
    aus dem Cache (z. B. Frontend bei reinem Backend-Commit) ist das älter als der
    Push. Der aktuell deployte Tag bleibt trotzdem: über den Cluster-Check bzw.
    --keep.
  - --apply bricht ab, wenn die laufenden Cluster-Tags nicht ermittelt werden
    konnten oder der Cluster-Check kein einziges Image dieser Registry fand
    (falscher Namespace/Cluster) – außer mit --no-cluster-check –, oder wenn die
    Registry kein Löschen erlaubt (REGISTRY_STORAGE_DELETE_ENABLED=true fehlt).

Cluster-Check und Rechte
  Ohne --namespace fragt das Skript alle Namespaces ab (braucht clusterweite
  Leserechte). Der Deploy-Workflow läuft mit der namespacebeschränkten
  ServiceAccount dms-deployer; dort --namespace dms setzen – dann werden nur
  Pods, CronJobs und Deployments dieses Namespace gelesen (mehr darf die Role
  nicht; Pods decken StatefulSets/DaemonSets mit ab).

Speicher wird erst frei, wenn danach auf dem Registry-Host der Garbage-Collect
läuft (während dessen keine Pushes!):

    docker exec <registry-container> registry garbage-collect /etc/docker/registry/config.yml
    docker restart <registry-container>

Zugangsdaten (nur fürs Löschen nötig, falls die Registry Auth verlangt):
REGISTRY_USER / REGISTRY_PASSWORD, sonst der Eintrag aus ~/.docker/config.json
(`docker login`). Es werden keine Zugangsdaten ausgegeben oder gespeichert.

Beispiele
    scripts/registry-prune.py --insecure                       # Trockenlauf
    scripts/registry-prune.py --insecure --keep-last 15 --apply
    scripts/registry-prune.py --insecure --namespace dms --keep "$TAG" --apply   # Deploy-Workflow
"""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
SHA_TAG = re.compile(r"[0-9a-f]{7,40}")
ZERO_DIGEST = "sha256:" + "0" * 64


class RegistryError(RuntimeError):
    pass


class Registry:
    """Minimaler Registry-v2-Client (nur Standardbibliothek)."""

    def __init__(self, url: str, insecure: bool, ip: str | None, auth: str | None):
        u = urlparse(url if "://" in url else "https://" + url)
        self.scheme = u.scheme
        self.host = u.hostname or ""
        self.port = u.port or (443 if u.scheme == "https" else 80)
        self.ip = ip
        self.auth = auth
        self.ctx = ssl.create_default_context()
        if insecure:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def _conn(self) -> http.client.HTTPConnection:
        if self.scheme != "https":
            return http.client.HTTPConnection(self.ip or self.host, self.port, timeout=30)
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=30, context=self.ctx)
        if self.ip:
            # Split-DNS: zur IP verbinden, Hostname für SNI/Host-Header behalten.
            sock = socket.create_connection((self.ip, self.port), timeout=30)
            conn.sock = self.ctx.wrap_socket(sock, server_hostname=self.host)
        return conn

    def request(self, method: str, path: str, accept: str | None = None):
        headers = {"Host": self.host, "User-Agent": "dms-registry-prune"}
        if accept:
            headers["Accept"] = accept
        if self.auth:
            headers["Authorization"] = "Basic " + self.auth
        conn = self._conn()
        try:
            conn.request(method, path, headers=headers)
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
        finally:
            conn.close()

    def get_json(self, path: str, accept: str | None = None) -> dict:
        status, _, body = self.request("GET", path, accept)
        if status != 200:
            raise RegistryError(f"GET {path} -> HTTP {status}")
        return json.loads(body)

    def tags(self, repo: str) -> list[str]:
        return self.get_json(f"/v2/{repo}/tags/list").get("tags") or []

    def describe(self, repo: str, tag: str) -> dict:
        """Digest, Kind-Manifeste, Blobs und Erstellungsdatum eines Tags."""
        status, headers, body = self.request("GET", f"/v2/{repo}/manifests/{tag}", MANIFEST_TYPES)
        if status != 200:
            raise RegistryError(f"Manifest {repo}:{tag} -> HTTP {status}")
        digest = headers.get("docker-content-digest", "")
        if not digest:
            raise RegistryError(f"Manifest {repo}:{tag} ohne Docker-Content-Digest")
        manifest = json.loads(body)
        info = {"tag": tag, "digest": digest, "children": [], "blobs": {}, "created": ""}
        images = [manifest]
        if "manifests" in manifest:  # OCI-Index / Manifest-Liste
            images = []
            for child in manifest["manifests"]:
                info["children"].append(child["digest"])
                images.append(
                    (child, self.get_json(f"/v2/{repo}/manifests/{child['digest']}", MANIFEST_TYPES))
                )
        for entry in images:
            child, img = entry if isinstance(entry, tuple) else ({}, entry)
            for layer in img.get("layers", []):
                info["blobs"][layer["digest"]] = layer.get("size", 0)
            cfg = img.get("config")
            if cfg:
                info["blobs"][cfg["digest"]] = cfg.get("size", 0)
                platform = child.get("platform", {})
                is_attestation = platform.get("os") == "unknown"
                if not info["created"] and not is_attestation:
                    try:
                        info["created"] = self.get_json(f"/v2/{repo}/blobs/{cfg['digest']}").get(
                            "created", ""
                        )
                    except (RegistryError, ValueError):
                        pass
        return info

    def delete_manifest(self, repo: str, digest: str) -> int:
        status, _, _ = self.request("DELETE", f"/v2/{repo}/manifests/{digest}")
        return status


def docker_config_auth(host: str) -> str | None:
    cfg = Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker")) / "config.json"
    try:
        auths = json.loads(cfg.read_text()).get("auths", {})
    except (OSError, ValueError):
        return None
    for key, value in auths.items():
        if urlparse(key if "://" in key else "https://" + key).hostname == host:
            return value.get("auth") or None
    return None


def referenced_in_manifests(root: Path, registry_host: str, repo: str) -> set[str]:
    """Tags, die in den k8s-Manifesten stehen (image: …:<tag> und kustomize newTag)."""
    found: set[str] = set()
    if not root.is_dir():
        return found
    image = re.compile(re.escape(f"{registry_host}/{repo}") + r":([A-Za-z0-9._-]+)")
    new_tag = re.compile(
        r"name:\s*" + re.escape(f"{registry_host}/{repo}") + r"\s*\n\s*newTag:\s*\"?([A-Za-z0-9._-]+)\"?"
    )
    for path in root.rglob("*"):
        if path.suffix not in {".yaml", ".yml"} or not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        found.update(image.findall(text))
        found.update(new_tag.findall(text))
    return found


def running_in_cluster(registry_host: str, namespace: str | None) -> dict[str, set[str]] | None:
    """Laufende/geplante Images aus dem Cluster: {repo: {tags}}; None = nicht ermittelbar."""
    if not shutil.which("kubectl"):
        return None
    if namespace:
        # Namespacebeschränkte Identität (dms-deployer): nur, was deren Role lesen darf.
        cmd = ["kubectl", "-n", namespace, "get", "pods,cronjobs,deployments", "-o", "json"]
    else:
        cmd = ["kubectl", "get", "pods,cronjobs,deployments,statefulsets,daemonsets", "-A", "-o", "json"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    pattern = re.compile(r'"image":\s*"' + re.escape(registry_host) + r'/([^":@]+):([^"@]+)"')
    result: dict[str, set[str]] = {}
    for repo, tag in pattern.findall(out):
        result.setdefault(repo, set()).add(tag)
    return result


def human(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e8 else f"{size / 1e6:.0f} MB"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--registry", default="https://registry.stoegerer-home.at")
    p.add_argument("--repo", action="append", help="Repo (mehrfach); Default: dms-backend, dms-frontend")
    p.add_argument("--keep-last", type=int, default=10, help="neueste N SHA-Tags je Repo behalten (Default 10)")
    p.add_argument("--keep", action="append", default=[], metavar="TAG", help="Tag immer behalten (mehrfach)")
    p.add_argument("--prune-named", action="store_true", help="auch Nicht-SHA-Tags (latest, 0.1.0 …) dürfen gelöscht werden")
    p.add_argument("--manifests", type=Path, default=Path(__file__).resolve().parent.parent / "deploy" / "k8s",
                   help="Verzeichnis mit k8s-Manifesten; dort referenzierte Tags bleiben")
    p.add_argument("--namespace", metavar="NS",
                   help="Cluster-Check nur in diesem Namespace (für namespacebeschränkte Identitäten); Default: alle")
    p.add_argument("--no-cluster-check", action="store_true", help="laufende Cluster-Tags NICHT per kubectl ermitteln")
    p.add_argument("--insecure", action="store_true", help="TLS-Zertifikat nicht prüfen (self-signed Registry)")
    p.add_argument("--ip", help="zu dieser IP verbinden (Split-DNS), Hostname bleibt für TLS/Host")
    p.add_argument("--apply", action="store_true", help="wirklich löschen (ohne: Trockenlauf)")
    args = p.parse_args()
    if args.keep_last < 1:
        p.error("--keep-last muss >= 1 sein")

    host = urlparse(args.registry if "://" in args.registry else "https://" + args.registry).hostname or ""
    auth = None
    if os.environ.get("REGISTRY_USER") and os.environ.get("REGISTRY_PASSWORD"):
        auth = base64.b64encode(
            f"{os.environ['REGISTRY_USER']}:{os.environ['REGISTRY_PASSWORD']}".encode()
        ).decode()
    elif args.apply:
        auth = docker_config_auth(host)
    reg = Registry(args.registry, args.insecure, args.ip, auth)
    repos = args.repo or ["dms-backend", "dms-frontend"]

    cluster = None if args.no_cluster_check else running_in_cluster(host, args.namespace)
    if args.no_cluster_check:
        print("Cluster-Check: übersprungen (--no-cluster-check).")
    elif cluster is None:
        print("Cluster-Check: kubectl nicht nutzbar – laufende Tags NICHT ermittelt.")
        if args.apply:
            print("ABBRUCH: --apply ohne Cluster-Check ist gesperrt. Auf einem Host mit kubectl "
                  "ausführen, laufende Tags per --keep nennen oder --no-cluster-check setzen.", file=sys.stderr)
            return 2
    else:
        print("Cluster-Check: " + (", ".join(f"{r}: {sorted(t)}" for r, t in sorted(cluster.items())) or "keine Images dieser Registry im Cluster gefunden"))
        if args.apply and not cluster:
            print("ABBRUCH: Der Cluster-Check fand kein Image dieser Registry – vermutlich falscher "
                  "Namespace oder Cluster. --namespace prüfen oder --no-cluster-check setzen.", file=sys.stderr)
            return 2

    plans = []
    for repo in repos:
        tags = reg.tags(repo)
        with ThreadPoolExecutor(8) as pool:
            infos = list(pool.map(lambda t, r=repo: reg.describe(r, t), tags))
        by_tag = {i["tag"]: i for i in infos}

        reasons: dict[str, str] = {}
        sha_tags = sorted((i for i in infos if SHA_TAG.fullmatch(i["tag"])), key=lambda i: i["created"], reverse=True)
        for i in sha_tags[: args.keep_last]:
            reasons[i["tag"]] = "neueste"
        if not args.prune_named:
            for i in infos:
                if not SHA_TAG.fullmatch(i["tag"]):
                    reasons[i["tag"]] = "benannt"
        for tag in referenced_in_manifests(args.manifests, host, repo):
            if tag in by_tag:
                reasons[tag] = "in Manifesten"
        for tag in (cluster or {}).get(repo, set()):
            if tag in by_tag:
                reasons[tag] = "läuft im Cluster"
        for tag in args.keep:
            if tag in by_tag:
                reasons[tag] = "--keep"

        protected_digests: set[str] = set()
        kept_blobs: dict[str, int] = {}
        for tag in reasons:
            protected_digests.add(by_tag[tag]["digest"])
            protected_digests.update(by_tag[tag]["children"])
            kept_blobs.update(by_tag[tag]["blobs"])

        delete, shared = [], []
        for i in infos:
            if i["tag"] in reasons:
                continue
            (shared if i["digest"] in protected_digests else delete).append(i)

        freed: dict[str, int] = {}
        for i in delete:
            for digest, size in i["blobs"].items():
                if digest not in kept_blobs:
                    freed[digest] = size

        print(f"\n=== {repo}: {len(tags)} Tags -> behalten {len(reasons) + len(shared)}, löschen {len(delete)}, "
              f"frei nach Garbage-Collect ca. {human(sum(freed.values()))} (obere Grenze)")
        for tag in sorted(reasons, key=lambda t: by_tag[t]["created"], reverse=True):
            print(f"  behalten  {tag:12s} {by_tag[tag]['created'][:19]:19s}  ({reasons[tag]})")
        for i in shared:
            print(f"  behalten  {i['tag']:12s} {i['created'][:19]:19s}  (gleicher Digest wie ein behaltener Tag)")
        if delete:
            dates = sorted(i["created"][:10] for i in delete if i["created"])
            span = f"{dates[0]} bis {dates[-1]}" if dates else "ohne Datum"
            print(f"  löschen   {len(delete)} Tags, erstellt {span}")
        plans.append((repo, delete, protected_digests))

    total = sum(len(d) for _, d, _ in plans)
    if not args.apply:
        print(f"\nTrockenlauf: {total} Tags würden gelöscht. Nichts geändert. Mit --apply ausführen, um zu löschen.")
        return 0
    if total == 0:
        print("\nNichts zu löschen.")
        return 0

    # Vorab prüfen, ob die Registry Löschen erlaubt – mit einem Digest, den es nicht gibt.
    probe = reg.delete_manifest(plans[0][0], ZERO_DIGEST)
    if probe == 405:
        print("\nABBRUCH: Die Registry erlaubt kein Löschen (HTTP 405). Am Registry-Container "
              "REGISTRY_STORAGE_DELETE_ENABLED=true setzen und neu starten.", file=sys.stderr)
        return 3
    if probe in (401, 403):
        print(f"\nABBRUCH: Löschen nicht autorisiert (HTTP {probe}). REGISTRY_USER/REGISTRY_PASSWORD "
              "setzen oder `docker login` auf diesem Host ausführen.", file=sys.stderr)
        return 3

    errors = 0
    for repo, delete, protected in plans:
        done: set[str] = set()
        for i in delete:
            for digest in [i["digest"], *i["children"]]:
                if digest in protected or digest in done:
                    continue
                status = reg.delete_manifest(repo, digest)
                done.add(digest)
                if status not in (202, 404):
                    errors += 1
                    print(f"  FEHLER {repo}:{i['tag']} {digest[:19]} -> HTTP {status}", file=sys.stderr)
        print(f"{repo}: {len(delete)} Tags entfernt ({len(done)} Manifeste).")
    print("\nSpeicher wird erst nach dem Garbage-Collect auf dem Registry-Host frei (siehe --help).")
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RegistryError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:  # Registry nicht erreichbar, TLS-Fehler, Timeout
        print(f"FEHLER: Registry nicht erreichbar: {exc}", file=sys.stderr)
        sys.exit(1)
