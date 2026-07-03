# Beitrag & Branch-/PR-Workflow

**Verbindliche Team-Konvention (STOAA-8):** Jede abgeschlossene Aufgabe endet mit einem **Pull Request** gegen [`Sepp1324/dms-stoegerer`](https://github.com/Sepp1324/dms-stoegerer). Es wird **nicht** direkt auf `main` gepusht.

## Ablauf pro Aufgabe
1. Aktuellen `main` ziehen: `git switch main && git pull`.
2. Feature-Branch anlegen: `git switch -c <typ>/<kurz-beschreibung>` (z. B. `feat/audit-trail`, `fix/doc-isolation`, `docs/…`, `chore/…`).
3. Arbeiten, committen (kleine, nachvollziehbare Commits).
4. Branch pushen: `git push -u origin <branch>`.
5. **Pull Request** gegen `main` öffnen — Titel = Aufgabe, Beschreibung = Was/Warum + Verweis auf das Issue.
6. Review durch CTO/QA; nach Freigabe **Squash-Merge**. CI (`.github/workflows/deploy.yml`) deployt beim Merge nach `main`.

## Konventionen
- Branch-Präfixe: `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`, `test/`.
- Keine Secrets in Commits/PRs. `deploy/k8s/secret.yaml` bleibt via `.gitignore` ausgeschlossen.
- Ein PR = eine Aufgabe. Große Vorhaben in Teilaufgaben/Teil-PRs schneiden.

> Diese Datei ist der erste Referenz-PR dieses Workflows und wurde selbst per PR eingebracht.
