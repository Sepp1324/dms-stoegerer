<!--
PR-Vorlage für dms-stoegerer. Kurz halten, Zutreffendes ankreuzen.
Konvention: eine Aufgabe → ein Branch → ein PR gegen main. Siehe CONTRIBUTING.md.
-->

## Ziel
<!-- Was löst dieser PR? Verweis auf Issue, z. B. STOAA-XX. -->

## Änderungen
<!-- Stichpunkte: betroffene Dateien/Bereiche. -->
-

## Checks vor dem Merge
- [ ] Backend: `python manage.py check` = 0 Probleme
- [ ] Backend: `python manage.py makemigrations --check` (keine offenen Migrationen)
- [ ] Bei Modelländerung: Migration erzeugt und mitgeliefert
- [ ] Frontend: `npm run build` (tsc + vite) erfolgreich
- [ ] Keine Secrets/API-Keys im Diff

## Deploy-Hinweis
<!-- Image-Rebuild nötig? Backfill-/Management-Command? reindex/reprocess? Nur-Doku? -->
-

## Review
- [ ] Review durch CTO/QA angefordert — Merge erst nach Freigabe

---
_Commit-Messages enden mit:_ `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
