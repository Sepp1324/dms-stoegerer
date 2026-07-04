# Beitrag & Branch-/PR-Workflow

**Verbindliche Team-Konvention (STOAA-8):** Jede abgeschlossene Aufgabe endet mit einem **Pull Request** gegen [`Sepp1324/dms-stoegerer`](https://github.com/Sepp1324/dms-stoegerer). Es wird **nicht** direkt auf `main` gepusht.

## Ablauf pro Aufgabe
1. Aktuellen `main` ziehen: `git switch main && git pull`.
2. Feature-Branch anlegen: `git switch -c <typ>/<kurz-beschreibung>` (z. B. `feat/audit-trail`, `fix/doc-isolation`, `docs/…`, `chore/…`).
3. Arbeiten, committen (kleine, nachvollziehbare Commits).
   - **Bei Django-Modell-Änderungen:** Migration **sofort** nach der Änderung erstellen (`python manage.py makemigrations`) und committen. Nicht aufschieben, sonst kollidiert die Nummer mit parallelen Branches.
4. Branch pushen: `git push -u origin <branch>`.
5. **Pull Request** gegen `main` öffnen — Titel = Aufgabe, Beschreibung = Was/Warum + Verweis auf das Issue.
   - **Vor dem PR (wichtig bei Migrations-Änderungen):** `git fetch origin main && git rebase origin/main` – so erkennst du Migrations-Konflikte **lokal**, bevor die CI rot wird.
6. Review durch CTO/QA; nach Freigabe **Squash-Merge**. CI (`.github/workflows/deploy.yml`) deployt beim Merge nach `main`.

## Konventionen
- Branch-Präfixe: `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`, `test/`.
- Keine Secrets in Commits/PRs. `deploy/k8s/secret.yaml` bleibt via `.gitignore` ausgeschlossen.
- Ein PR = eine Aufgabe. Große Vorhaben in Teilaufgaben/Teil-PRs schneiden.
- **Migrations-Konflikte vermeiden:** Immer vom aktuellen `main` branchen, Migration sofort erstellen, vor dem PR rebasen. Bei Konflikt: `python manage.py makemigrations --merge` (siehe [README.md](README.md#django-migrationen-konflikt-prävention)).

> Diese Datei ist der erste Referenz-PR dieses Workflows und wurde selbst per PR eingebracht.
