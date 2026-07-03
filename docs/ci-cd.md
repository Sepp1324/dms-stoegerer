# CI/CD – automatischer Build & Deploy

Beim **Merge nach `main`** baut ein **self-hosted GitHub-Actions-Runner** im
Heimnetz die Images, pusht sie in die Registry und rollt sie im k3s-Cluster aus.

Workflows:
- [`.github/workflows/pr-check.yml`](../.github/workflows/pr-check.yml) – **PR-Gate** (bei `pull_request`)
- [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) – **Deploy** (bei Push nach `main`)

```
PR öffnen → PR-Check  ─▶  GitHub Actions (Runner auf k3s-01, Label "dms")
                           1. Backend-Image bauen (Git-SHA-Tag)
                           2. Django-Check (Systemkonfiguration)
                           3. makemigrations --check (fehlende Migrations)
                           4. Multi-Leaf-Detection (Migrations-Kollision)
                           ✅ Grün → Merge freigegeben
                           ❌ Rot → Merge blockiert

PR mergen → main  ─▶  GitHub Actions (Runner auf k3s-01, Label "dms")
                        1. Django-Check im frischen Image
                        2. Multi-Leaf-Detection (fail-fast vor Push)
                        3. docker build backend + frontend  (Tag = Git-SHA)
                        4. docker push → registry.stoegerer-home.at
                        5. Manifeste rendern, Image-Tags → Git-SHA, kubectl apply
                        6. rollout status abwarten
```

Der Image-Tag ist der **kurze Git-SHA** – jeder Deploy ist damit eindeutig und
reproduzierbar (kein `latest`).

**PR-Gate-Absicherung:** Der `pr-check`-Workflow verhindert, dass parallele
Feature-Branches mit je eigener Migration gleicher Nummer (z. B. `0007`-Kollision,
Multi-Leaf-Nodes) gemergt werden. Django's `migrate --plan` erkennt solche
Kollisionen; der Workflow schlägt fehl und blockiert den Merge. Lösung: Einen
Branch rebasen und die Migration neu nummerieren lassen.

---

## 1. Voraussetzungen auf dem Runner-Host (k3s-01)

Der Runner nutzt das, was auf dem Node schon da ist:

| Anforderung | Prüfen / Einrichten |
|---|---|
| **Docker** nutzbar für den Runner-Nutzer | `sudo usermod -aG docker <runner-user>` (neu einloggen) |
| **Registry-Login** (Push braucht Auth) | `docker login registry.stoegerer-home.at` (einmalig, wird in `~/.docker/config.json` gespeichert) |
| **Registry-Zertifikat** vertraut | `insecure-registries` in `/etc/docker/daemon.json` (hast du für den Push schon gesetzt) |
| **kubectl** auf `PATH` | k3s legt `/usr/local/bin/kubectl` an → `kubectl version` testen |
| **Kubeconfig lesbar** für den Runner-Nutzer | siehe unten |
| **git** installiert | `git --version` |

### Kubeconfig-Zugriff

Der Workflow setzt `KUBECONFIG=/etc/rancher/k3s/k3s.yaml`. Diese Datei gehört
`root` (Mode 600). Dem Runner-Nutzer Lesezugriff geben – am einfachsten k3s
anweisen, sie gruppenlesbar zu schreiben:

```bash
# k3s dauerhaft konfigurieren
echo "write-kubeconfig-mode: \"0644\"" | sudo tee -a /etc/rancher/k3s/config.yaml
sudo systemctl restart k3s
ls -l /etc/rancher/k3s/k3s.yaml     # sollte -rw-r--r-- sein
```

> Alternativen: Runner als `root` laufen lassen (einfach, aber mehr Rechte) oder
> die Kubeconfig in das Home des Runner-Nutzers kopieren und `KUBECONFIG` im
> Workflow entsprechend anpassen.

---

## 2. Runner registrieren

**GitHub:** Repo `Sepp1324/dms-stoegerer` → **Settings → Actions → Runners →
New self-hosted runner → Linux / x64**. GitHub zeigt dir die konkreten Download-
und `config.sh`-Befehle **mit einem Registrierungs-Token**. Beim Konfigurieren:

- **Labels:** unbedingt `dms` ergänzen (der Workflow nutzt `runs-on: [self-hosted, dms]`).
- **Work folder:** Standard ist ok.

Beispiel (Token/URL aus der GitHub-Seite übernehmen):

```bash
# Auf k3s-01, als der Runner-Nutzer
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -o runner.tar.gz -L https://github.com/actions/runner/releases/latest/download/actions-runner-linux-x64.tar.gz
tar xzf runner.tar.gz

./config.sh --url https://github.com/Sepp1324/dms-stoegerer \
  --token <REGISTRIERUNGS-TOKEN> \
  --labels dms \
  --name k3s-01
```

### Als Dienst laufen lassen (Autostart)

```bash
sudo ./svc.sh install <runner-user>
sudo ./svc.sh start
sudo ./svc.sh status
```

Danach erscheint der Runner in GitHub unter *Settings → Actions → Runners* als
**idle** (grün).

---

## 3. Nutzung

1. Einen **PR** gegen `main` erstellen und nach Review mergen — kein Direkt-Push
   auf `main` (verbindlicher Standard: CONTRIBUTING.md). Push-Credential: docs/secrets.md.
2. Der **PR-Check-Workflow** läuft automatisch und prüft:
   - Django-Systemkonfiguration (`manage.py check`)
   - Fehlende Migrations (`makemigrations --check`)
   - **Migrations-Kollisionen** (Multi-Leaf-Nodes durch parallele PRs)
3. Nach grünem PR-Check: Merge → der **Deploy-Workflow** läuft auf `main`.
4. Nach erfolgreichem Deploy laufen die neuen Images im Cluster; der Schritt
   *Zusammenfassung* zeigt `kubectl get pods`.

**Manueller Trigger / erneuter Lauf:** im Actions-Tab den letzten Lauf öffnen →
*Re-run jobs*.

**Rollback:** `git revert <commit>` und mergen → die Pipeline deployt den
vorherigen Stand. (Alternativ einen älteren Lauf erneut ausführen.)

### Branch Protection (empfohlen)

Um sicherzustellen, dass der PR-Check vor jedem Merge läuft und Migrations-Kollisionen
blockiert werden, sollte **Branch Protection** für `main` aktiviert werden:

**GitHub → Repo Settings → Branches → Add branch protection rule:**

- **Branch name pattern:** `main`
- **☑ Require status checks to pass before merging**
  - **☑ Require branches to be up to date before merging**
  - **Status checks that are required:** `migration-check` (der Job-Name aus `pr-check.yml`)
- **☑ Require a pull request before merging** (verhindert Direct-Push)

**Hinweis für Owner:** Die Branch-Protection-Aktivierung erfordert Admin-Rechte
und muss vom Repository-Owner durchgeführt werden. Der PR-Check-Workflow ist
bereits implementiert und läuft bei jedem PR; Branch Protection macht ihn zu
einer harten Anforderung für den Merge.

---

## 4. Sicherheitshinweis

Der Runner kann **Images bauen** (Docker ≈ root) und **ins Cluster deployen**
(cluster-admin über die k3s-Kubeconfig). Wer nach `main` pushen kann, kann damit
Code auf dem Node ausführen. Für ein privates Homelab-Repo ist das vertretbar –
aber:

- **Branch Protection** auf `main` aktivieren (Repo → Settings → Branches): nur
  über PRs mergen, keine direkten Pushes von Fremden.
- Repo privat halten.
- Bei mehreren Nutzern ggf. den Runner auf ein eigenes, weniger privilegiertes
  Service-Konto stellen und die RBAC auf den `dms`-Namespace einschränken (statt
  der cluster-admin-Kubeconfig).

---

## 5. Fehlersuche

| Symptom | Ursache / Lösung |
|---|---|
| Workflow startet nicht | Runner offline (GitHub → Runners) oder Label `dms` fehlt |
| `permission denied` bei docker | Runner-Nutzer nicht in Gruppe `docker` (neu einloggen nach `usermod`) |
| `denied` / `unauthorized` beim Push | `docker login registry.stoegerer-home.at` fehlt/abgelaufen |
| `x509`/Zertifikat beim Push | `insecure-registries` in `/etc/docker/daemon.json` + `systemctl restart docker` |
| `kubectl` kann nicht verbinden | Kubeconfig nicht lesbar → §1 (write-kubeconfig-mode) |
| `rollout status` Timeout | Pod-Fehler → `kubectl -n dms describe pod …`; siehe deploy/k8s/README.md §15 |
