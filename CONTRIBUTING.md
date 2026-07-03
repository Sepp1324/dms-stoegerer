# Beitrags-Workflow — dms-stoegerer

Verbindlicher Team-Standard für alle Beitragenden (Menschen **und** Agenten).
Ziel: jede abgeschlossene Aufgabe endet mit einem **Pull Request** gegen
`main` — **kein Direkt-Commit auf `main`**.

## Grundregel: eine Aufgabe → ein Branch → ein PR → Review → Merge

```
Issue (z. B. STOAA-XX)
   └─▶ Branch von main:  feat/… | fix/… | docs/… | chore/…
          └─▶ Änderung + Commits (Checks lokal grün)
                 └─▶ Push → Pull Request gegen main
                        └─▶ Review (QA/CTO)  ──▶  Merge (Squash oder Merge-Commit)
                               └─▶ CI (deploy.yml) baut & rollt automatisch aus
```

- **Nie** direkt auf `main` committen oder pushen. `main` ist geschützt
  (Branch Protection, siehe `docs/ci-cd.md` §4).
- **Ein PR pro Aufgabe.** Kleine, fokussierte Änderungen; keine ungefragten
  Refactorings im selben PR.
- Merge nach `main` löst den Deploy aus (self-hosted Runner → k3s). Also erst
  mergen, wenn Review bestanden und Checks grün sind.

### Branch-Namensschema
`<typ>/<kurzbeschreibung>` bzw. `<typ>/stoaa-<nr>-<kurz>`, z. B.
`feat/stoaa-4-email-ingestion`, `fix/stoaa-7-owner-isolation`,
`docs/stoaa-10-pr-convention`. Typen: `feat`, `fix`, `docs`, `chore`, `refactor`.

### Commit-Messages
Kurz, im Imperativ, auf Deutsch. Jede Commit-Message endet mit:

```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

## Checks vor dem PR (Definition of Done)

| Bereich | Kommando | Erwartung |
|---|---|---|
| Backend | `python manage.py check` | 0 Probleme |
| Backend | `python manage.py makemigrations --check` | keine offenen Migrationen |
| Backend | bei Modelländerung | Migration erzeugt & mitgeliefert |
| Frontend | `npm run build` (tsc + vite) | erfolgreich |
| Alle | Diff prüfen | **kein** Secret/API-Key enthalten |

Der PR-Text nennt außerdem den **Deploy-Hinweis** (Image-Rebuild nötig?
Backfill-/Management-Command? reindex/reprocess? oder Nur-Doku).
Die Vorlage dafür liegt in `.github/PULL_REQUEST_TEMPLATE.md` und wird beim
Öffnen eines PR automatisch eingefügt.

## Review & Merge

- Review durch **CTO oder QA**. Erst nach Freigabe wird gemergt (Freigabe-Gate).
- Nichts durchwinken, dessen Checks/Build rot sind.
- Nach dem Merge übernimmt die CI (`.github/workflows/deploy.yml`) Build & Rollout.
  Rollback per `git revert <commit>` + Merge.

## Beispiel-Flow (Referenz)

```bash
# 1. Aktuellen main holen und Branch anlegen
git switch main && git pull --ff-only
git switch -c feat/stoaa-42-audit-view

# 2. Arbeiten, committen (Checks lokal grün halten)
python manage.py check
python manage.py makemigrations --check
(cd frontend && npm run build)
git add -A
git commit   # Message endet mit Co-Authored-By: …

# 3. Push und PR öffnen
git push -u origin feat/stoaa-42-audit-view
gh pr create --base main --fill    # oder PR über die GitHub-UI öffnen

# 4. Review abwarten (QA/CTO) → nach Freigabe Merge → CI deployt automatisch
```

## Zugangsdaten / Push-Credential

Für `git push` / PR-Operationen wird ein GitHub-**Token** benötigt. Dieser liegt
**ausschließlich** im Secret-Store — **nie** in Git, Issues oder Kommentaren.
Einrichtung, Rotation und die genaue Ablage sind in **`docs/secrets.md`**
beschrieben. Ohne gültiges Credential im Secret-Store bleibt der Push/PR-Schritt
blockiert; Branch und Commits können lokal trotzdem vorbereitet werden.
