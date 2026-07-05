# DMS – Konzept & Architektur

> Eigenes Dokumenten-Management-System, das die Stärken von **paperless-ngx**
> (offen, modern, starke OCR/ML, gute API) und **ecoDMS** (revisionssicher,
> versioniert, feingranulare Rechte, regelbasierte Klassifizierung, Workflows)
> vereint.

**Status:** Entwurf · **Datum:** 2026-07-02 · **Zielgruppe:** Familie (2–3 Nutzer)

---

## 1. Leitidee & Nicht-Ziele

**Leitidee:** Ein selbst-gehostetes DMS, das im Alltag so leicht bedienbar ist
wie paperless, aber Dokumente *nachvollziehbar und unveränderbar* archiviert wie
ecoDMS – ohne dessen schwerfälligen Java-Client und geschlossenes Ökosystem.

**Bewusste Nicht-Ziele (zumindest anfangs):**
- Keine volle GoBD-Zertifizierung / kein Verfahrensdokumentations-Overhead – aber
  die Architektur wird so gebaut, dass Revisionssicherheit später *nachrüstbar*
  ist, ohne alles umzuwerfen.
- Kein Mandanten-/Enterprise-Rechtemodell. Rollen bleiben einfach (Admin / Nutzer / Gast).
- Keine Skalierung auf hunderte parallele Nutzer.

---

## 2. Was wir von wem übernehmen

| Fähigkeit | Vorbild | Umsetzung bei uns |
|---|---|---|
| Consume-Ordner + Upload + E-Mail-Ingestion | paperless | ✅ übernehmen |
| OCR-Pipeline (OCRmyPDF/Tesseract), PDF/A | paperless | ✅ übernehmen |
| Volltextsuche | paperless | ✅ übernehmen (PostgreSQL FTS) |
| Auto-Klassifizierung per ML | paperless | ✅ als *Vorschlag*, nicht bindend |
| Moderne SPA-UI + starke REST-API | paperless | ✅ übernehmen |
| **Regelbasierte Klassifizierungsvorlagen** | ecoDMS | ✅ übernehmen (deterministisch + erklärbar) |
| **Versionierung von Dokumenten** | ecoDMS | ✅ Kern-Datenmodell |
| **Revisionssichere Ablage (WORM + Hash-Kette)** | ecoDMS | ⏳ Architektur ab Tag 1, Aktivierung später |
| **Audit-Trail / lückenloses Log** | ecoDMS | ✅ ab Tag 1 (billig, hoher Nutzen) |
| **Feingranulare Rechte** | ecoDMS | 🟡 vereinfacht (Rollen + Ordner-/Tag-ACL) |
| **Workflows / Freigaben / Wiedervorlage** | ecoDMS | ⏳ spätere Ausbaustufe |
| Aufbewahrungsfristen / Retention | ecoDMS | ⏳ spätere Ausbaustufe |

Legende: ✅ MVP · 🟡 vereinfacht im MVP · ⏳ spätere Stufe

---

## 3. Technologie-Entscheidung

### Empfehlung: **Django + Django REST Framework (DRF)**

Obwohl du „FastAPI/Django" offen gelassen hast, empfehle ich klar **Django**:

- **Batteries included** für genau diese Domäne: Auth, Rollen/Permissions,
  Migrationen, und vor allem das **Admin-Panel** – damit verwalten wir Tags,
  Dokumenttypen, Korrespondenten, Nutzer & Regeln *ohne dafür UI bauen zu müssen*.
- Es ist **erprobt für exakt diesen Use-Case** – paperless-ngx beweist, dass
  Django + Celery ein DMS trägt. Wir stehen auf Schultern, statt Rad neu zu erfinden.
- Der einzige „Nachteil" (synchrone Views) ist irrelevant: schwere Arbeit (OCR)
  läuft ohnehin asynchron in **Celery**-Workern, nicht im Request.

**FastAPI** wäre die Alternative, wenn du bewusst async-first und ein sehr
schlankes API-Backend willst – dann verlieren wir aber Admin-Panel & viel
Fertig-Infrastruktur und bauen mehr selbst. Für ein Familien-DMS: unnötig.
👉 *Sag Bescheid, wenn du lieber FastAPI willst – dann passe ich das Konzept an.*

### Stack im Überblick

```
Frontend      : React (Vite) + TypeScript  – SPA, spricht nur die REST-API
Backend       : Django 5 + Django REST Framework
Task-Queue    : Celery + Redis            – OCR, Klassifizierung, E-Mail-Abruf
Datenbank     : PostgreSQL 16             – Metadaten + Volltextsuche (FTS)
Datei-Storage : lokales Filesystem        – strukturierte Ablage, später WORM/S3
OCR           : OCRmyPDF + Tesseract      – erzeugt durchsuchbares PDF/A
Auth          : Django-Sessions / JWT     – simple Rollen: Admin/Nutzer/Gast
Deployment    : Docker Compose            – ein Befehl, alles läuft
```

Warum diese Wahl:
- **PostgreSQL-FTS statt Elasticsearch:** für 2–3 Nutzer völlig ausreichend,
  kein zusätzlicher schwergewichtiger Dienst. Elasticsearch bleibt optional.
- **React statt Django-Templates:** moderne, schnelle UI wie bei paperless;
  Backend bleibt reines API → sauber trennbar, später auch Mobile-App möglich.

---

## 4. Architektur (Komponenten)

```
                 ┌──────────────┐
   Browser  ───▶ │  React SPA   │
                 └──────┬───────┘
                        │ REST/JSON (JWT)
                 ┌──────▼───────┐        ┌───────────────┐
                 │ Django + DRF │◀──────▶│  PostgreSQL   │
                 │   (API)      │        │ Metadaten+FTS │
                 └──────┬───────┘        └───────────────┘
                        │ enqueue
                 ┌──────▼───────┐        ┌───────────────┐
                 │    Redis     │◀──────▶│ Celery Worker │
                 │  (Broker)    │        │ OCR/Klassifiz.│
                 └──────────────┘        └──────┬────────┘
                                                │
   Consume-Ordner / Upload / IMAP ──────────────┘
                                                │
                                         ┌──────▼────────┐
                                         │ Datei-Storage │
                                         │ (originals/    │
                                         │  archive/)     │
                                         └───────────────┘
```

**Ingestion-Wege (alle enden im selben Pipeline-Task):**
1. Web-Upload
2. Überwachter **Consume-Ordner** (z. B. vom Scanner beschickt)
3. **E-Mail-Postfach** (IMAP-Abruf, Anhänge)

**Verarbeitungs-Pipeline (Celery):**
```
Datei rein → Hash bilden (Dedup-Check) → OCR (→ PDF/A + Text) →
Metadaten extrahieren → Klassifizierungsregeln anwenden →
ML-Vorschläge ergänzen → in DB + Storage ablegen → Audit-Log-Eintrag
```

**Fehler-/Retry-Layer der Pipeline (STOAA-228):** Auf der linearen Erfolgs-State-
Machine (`processing_state`: `uploaded → … → ready`) sitzt ein Fehler-/Retry-Layer
mit zwei zusätzlichen `processing_state`-Werten: `failed` und `retry_pending`.
Schlägt ein Schritt fehl, markiert `mark_processing_failed()` die Version als
`failed` (samt `processing_error`, `processing_failed_step`, `processing_failed_at`)
und die Pipeline liefert ein strukturiertes Fehlerergebnis zurück statt zu
werfen – das Dokument bleibt sichtbar `failed`. Ein Retry (`retry_version()` bzw.
`manage.py retry_processing --failed`) zählt `processing_attempts` hoch
(`failed → retry_pending`), setzt `processing_state` auf die **Vorbedingung** des
fehlgeschlagenen Schritts (z. B. `hashed` vor dem OCR-Schritt) und läuft die
Pipeline ab dort erneut – der Schritt führt seine eigene `transition_to(RUNNING)`
aus (`retry_pending → hashed → ocr_running → …`). Bewusst über den bestehenden
`processing_state` **statt `Document.status`**: Fehler-/Retry sind ein technisches
Verarbeitungsdetail und dürfen von der fachlichen Freigabe (`Document.status`)
entkoppelt bleiben. WORM/`ready`-Versionen werden nie auf `failed` gesetzt.

---

## 5. Datenmodell (Kern)

Zentrale Idee gegenüber paperless: **Dokument ≠ Datei**. Ein *Document* ist ein
logisches Objekt mit **mehreren Versionen** (Files). Das macht Versionierung und
spätere Revisionssicherheit erst möglich.

```
Document
  ├─ id, title, created_at, added_at
  ├─ correspondent  → Correspondent      (wer? Absender/Firma)
  ├─ document_type  → DocumentType        (Rechnung, Vertrag, ...)
  ├─ tags           → Tag[* ]             (frei, farbig, hierarchisch möglich)
  ├─ storage_path   → StoragePath         (Ablage-Regel)
  ├─ owner          → User                (Eigentümer)
  ├─ current_version→ DocumentVersion
  └─ custom_fields  → { schlüssel: wert } (z. B. Betrag, Rechnungsnr., Datum)

DocumentVersion            ← Kern für Versionierung & Revisionssicherheit
  ├─ document → Document
  ├─ version_no (1,2,3…)
  ├─ file_path            (Original)
  ├─ archive_path         (OCR'tes PDF/A)
  ├─ sha256               (Integritäts-Hash)
  ├─ prev_hash            (Hash-Kette → Manipulationserkennung)
  ├─ ocr_text             (Volltext, in FTS indiziert)
  ├─ mime_type, size, page_count
  ├─ created_by, created_at
  └─ is_immutable (bool)  (WORM-Flag; später erzwungen)

AuditLogEntry              ← lückenloses, append-only Protokoll
  ├─ timestamp, actor(User), action, object, before/after (JSON)
  └─ (später: signiert / Hash-verkettet)

Correspondent, DocumentType, Tag, StoragePath   (wie paperless)

ClassificationRule         ← ecoDMS-artige Klassifizierungsvorlage
  ├─ name, priority, enabled
  ├─ match: Bedingungen (Text enthält / Regex / Absender / Betreff …)
  └─ then:  setze document_type, tags, correspondent, custom_fields …

User, Role (Admin/User/Guest), ACL (optional pro Tag/StoragePath)
```

**Custom Fields** (typisiert: Text/Zahl/Datum/Währung/Boolean) sind das, was
paperless lange fehlte und ecoDMS als „Klassifizierungsattribute" stark macht –
z. B. „Rechnungsbetrag", „Fälligkeitsdatum", „Vertragsnummer".

### 5.1 Eigentümer-Zuordnung & Triage (Owner-Isolation)

Jeder Nutzer sieht/verwaltet ausschließlich **eigene** Dokumente
(`owner`-Isolation, STOAA-7); DMS-Admins sehen alles. Damit darf kein
Ingest-Pfad ein Dokument still ohne Eigentümer aufnehmen – sonst wäre es für
den vorgesehenen Nutzer unsichtbar. Regeln:

- **E-Mail-Ingest:** Der `owner` des `MailAccount` wird durchgereicht. Ist er
  leer, greift die Env-Var **`MAIL_DEFAULT_OWNER`** (Username). Bei genutztem
  Fallback wird `AuditLogEntry action=owner_fallback` protokolliert.
- **Consume-Ordner (Flat-Modus):** Ohne Per-User-Ordner greift analog
  **`CONSUME_DEFAULT_OWNER`** (Username). Der **Per-User-Modus**
  (`CONSUME_PER_USER=true`, Ordnername = Username) bleibt unverändert und setzt
  den Owner ohnehin selbst.
- **Triage-Zustand:** Bleibt `owner=None` (kein Konto-Owner **und** kein/kein
  auflösbarer Default-Owner), ist das ein **bewusster, admin-sichtbarer
  Triage-Zustand** – protokolliert als `AuditLogEntry action=triage_ingest`
  (kein stilles `owner=None` ohne Spur). Ein unbekannter Default-Owner-Username
  bricht den Ingest nicht ab (Warn-Log) und führt in die Triage.
- **Triage sichten & zuweisen (Admin):** `GET /api/documents/?owner=none`
  listet für Admins die eigentümerlosen Dokumente (für Nicht-Admins wirkungslos,
  Isolation bleibt dicht). `POST /api/documents/{id}/set-owner/` mit Body
  `{"owner": <userId|username>}` weist den Eigentümer zu (nur `is_dms_admin`,
  sonst 403; protokolliert `owner_assigned`).

**Empfehlung Betrieb:** `CONSUME_PER_USER=true` setzen und jedem `MailAccount`
einen `owner` geben – dann ist die Triage die Ausnahme, nicht der Normalfall.
**Deployment-Hinweis:** Die neuen Env-Vars `MAIL_DEFAULT_OWNER` /
`CONSUME_DEFAULT_OWNER` müssen in der Backend-Image-/Deployment-Env gesetzt
werden. **Keine Migration** nötig (reine Settings + Query-/View-Logik).

---

## 6. Die drei Killer-Features (unser Alleinstellungsmerkmal)

1. **Erklärbare, regelbasierte Klassifizierung + ML-Vorschlag kombiniert.**
   Regeln sind deterministisch & nachvollziehbar („warum wurde das getaggt?"),
   ML liefert nur *Vorschläge* die man annimmt. paperless ist rein ML (Blackbox),
   ecoDMS rein regelbasiert – wir bieten beides transparent nebeneinander.

2. **Echte Versionierung mit Integritäts-Hash-Kette.**
   Jede Änderung erzeugt eine neue, prüfbare Version. Grundlage für spätere
   Revisionssicherheit – ohne teure Zertifizierung, aber technisch sauber.

3. **Ein Backend, viele Wege rein, moderne UI.** Upload, Scan-Ordner, E-Mail –
   alles landet in einer erklärbaren Pipeline, bedienbar über eine schnelle SPA.

---

## 7. Roadmap in Ausbaustufen

**Stufe 0 – Gerüst**
- Docker-Compose (Django, Postgres, Redis, Celery, Frontend-Dev)
- Datenmodell + Migrationen, Django-Admin nutzbar
- Auth + Rollen

**Stufe 1 – MVP „paperless-Kern"**
- Upload + Consume-Ordner → OCR-Pipeline → durchsuchbares PDF/A
- Tags / Korrespondenten / Dokumenttypen / Custom Fields
- Volltextsuche + Filter, Dokument-Ansicht in der SPA

**Stufe 2 – „ecoDMS-Stärken"**
- Versionierung (DocumentVersion, Hash-Kette) sichtbar in der UI
- Regelbasierte Klassifizierung (ClassificationRule) + ML-Vorschläge
- Audit-Trail-Ansicht

**Stufe 3 – E-Mail-Ingestion + Automatisierung**
- ✅ IMAP-Abruf: konfiguriertes Postfach (Admin: „E-Mail-Konto"), Passwort aus
  k8s-Secret (`password_env`). Celery-Beat ruft periodisch ab, Anhänge (PDF/Bild)
  durchlaufen die bestehende Pipeline; Idempotenz über Message-ID + Hash-Dedup;
  Absender → Correspondent-Vorschlag. (STOAA-4)
- ⬜ E-Mail-spezifische Regeln (Betreff→Tag), OAuth-Postfächer – spätere Stufe.

**Stufe 4 – Revisionssicherheit & Workflows**
- WORM/Immutable-Storage erzwingen, Retention/Aufbewahrungsfristen
- Freigabe-Workflows, Wiedervorlage/Erinnerungen

---

## 8. Entscheidungen

**Getroffen (2026-07-02):**
- ✅ **Stack:** Django + DRF (Backend) + **React/Vite/TS** (SPA-Frontend).
- ✅ **Migration:** Import aus **paperless-ngx** (Export-API). ecoDMS wird
  vorerst *nicht* migriert.

**Getroffen (2026-07-02, Fortsetzung):**
- ✅ **Ablagestruktur:** `archive/{jahr}/{korrespondent}/{titel}.pdf` (Basis für
  `StoragePath.path_template`). Wichtig fürs Backup.
- ✅ **paperless-Import-Umfang:** Nur **Dateien + Kern-Metadaten** (Titel, Datum,
  Korrespondent, Dokumenttyp). Tags/Custom Fields werden *nicht* 1:1 migriert.

**Getroffen (2026-07-03):**
- ✅ **Beitrags-Workflow (verbindlich):** Jede Aufgabe endet mit einem **Pull
  Request** gegen `main` — kein Direkt-Commit auf `main`. Ablauf:
  Branch je Aufgabe → PR → Review (QA/CTO) → Merge → CI deployt. Details in
  [`CONTRIBUTING.md`](CONTRIBUTING.md); Push-Credential-Ablage in
  [`docs/secrets.md`](docs/secrets.md) (Token nur im Secret-Store, nie in Git).

**Noch offen (nicht blockierend):**
1. **Backup-Strategie:** Wie sicherst du heute paperless/ecoDMS? Das DMS sollte
   sich in dein bestehendes Backup einfügen.
```
