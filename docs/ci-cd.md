# CI/CD – automatischer Build & Deploy

Beim **Merge nach `main`** baut ein **self-hosted GitHub-Actions-Runner** im
Heimnetz die Images, pusht sie in die Registry und rollt sie im k3s-Cluster aus.

Workflow: [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml)

```
PR mergen → main  ─▶  GitHub Actions (Runner auf k3s-01, Label "dms")
                        1. Django-Check im frischen Image
                        2. docker build backend + frontend  (Tag = Git-SHA)
                        3. docker push → registry.stoegerer-home.at
                        4. Manifeste rendern, Image-Tags → Git-SHA, kubectl apply
                        5. rollout status abwarten
```

Der Image-Tag ist der **kurze Git-SHA** – jeder Deploy ist damit eindeutig und
reproduzierbar (kein `latest`).

---

## Test-Gate (PR & Deploy)

Damit QA für Backend-PRs ein **echtes dynamisches Grün-Signal** hat, läuft die
Django-Testsuite in der CI – im gebauten Backend-Image gegen eine wegwerfbare
**Postgres 16** (SQLite scheidet aus, weil das Backend `django.contrib.postgres`
für die Volltextsuche nutzt).

Workflow: [`.github/workflows/pr-checks.yml`](../.github/workflows/pr-checks.yml)
Runner-Skript: [`backend/ci/run-tests.sh`](../backend/ci/run-tests.sh)

```
Pull Request → main  ─▶  pr-checks.yml (Runner "dms")
   backend-tests:    1. docker build backend  (flüchtiger CI-Tag)
                     2. Wegwerf-Postgres 16 starten
                     3. python manage.py check
                     4. python manage.py makemigrations --check --dry-run
                     5. python manage.py test
   frontend-build:   docker build frontend  (npm install + tsc -b && vite build)
   pr-checks:        aggregierendes Gate (needs: backend-tests, frontend-build)
```

- **PR-Gate:** `pr-checks.yml` läuft auf `pull_request → main`. Ein roter Job
  macht den PR – via **Branch-Protection** – nicht mergebar. **Kein Deploy** in
  diesem Workflow.
- **Deploy-Gate:** `deploy.yml` (Push nach `main`, unverändert) führt Check +
  `run-tests.sh` vor dem `docker push` erneut aus. So blockt ein roter Test auch
  bei direktem Push nach `main` den Rollout (zweites Sicherheitsnetz).

> **Branch-Protection einrichten (einmalig, macht der Owner):**
> GitHub → **Settings → Branches** → Regel für `main` → „Require status checks
> to pass before merging“ → Status-Check **`pr-checks`** auswählen. Erst danach
> ist ein PR mit rotem Gate nicht mehr mergebar. (`pr-checks` wird nur grün,
> wenn `backend-tests` **und** `frontend-build` grün sind.)

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
2. Der Merge löst den Workflow aus – live verfolgbar unter dem **Actions**-Tab
   des Repos.
3. Nach erfolgreichem Lauf laufen die neuen Images im Cluster; der Schritt
   *Zusammenfassung* zeigt `kubectl get pods`.

**Manueller Trigger / erneuter Lauf:** im Actions-Tab den letzten Lauf öffnen →
*Re-run jobs*.

**Rollback:** `git revert <commit>` und mergen → die Pipeline deployt den
vorherigen Stand. (Alternativ einen älteren Lauf erneut ausführen.)

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
