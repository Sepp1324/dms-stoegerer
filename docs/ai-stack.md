# Semantik-/KI-Stack: Suche, Auto-Ablage, Dubletten, Agent

Dieses Dokument beschreibt die semantischen Funktionen des DMS, ihre Stellschrauben
und die Betriebs-Runbooks. Alle Schwellwerte sind **per Env** justierbar (ConfigMap
`dms-config`) – kein Image-Rebuild nötig, sie greifen beim nächsten Rollout.

## Überblick

| Funktion | Was sie tut | Endpoint / UI |
|---|---|---|
| **Embeddings** | zerlegt OCR-Text in Chunks, rechnet lokale Vektoren (fastembed/e5), speichert sie in pgvector | – (Pipeline) |
| **Smart-Suche** | Volltext **+** Bedeutung, zu einem Ranking fusioniert (RRF) | `POST /api/search/hybrid/` · Button „🔎 Smart-Suche" |
| **Auto-Ablage** | schlägt Ordner/Tags/Korrespondent/Typ aus ähnlichen Dokumenten vor (kNN) | `GET /documents/{id}/filing-suggestions/` · Detail-Tab „Ähnliche" |
| **Autopilot** | wendet hoch-sichere Vorschläge automatisch an (Opt-in) bzw. per Batch | `POST /documents/auto-file-batch/` · „🗂️ Posteingang aufräumen" |
| **Dubletten** | findet inhaltliche Beinah-Duplikate (Cosine **+** Lexik) | `GET /documents/{id}/duplicates/`, `/documents/duplicate-report/` |
| **Copilot-Agent** | schlägt aus einer Anweisung Aktionen vor, führt sie nach Bestätigung aus | `POST /agent/plan|execute|undo/` · Copilot-Seite |
| **Familien-Freigabe** | Dokumente/Ordner für den Haushalt lesbar machen | `POST /documents/{id}/share-household/`, `/households/*` |

## Embeddings (Fundament)

- **Modell:** `intfloat/multilingual-e5-large` (1024-dim) via **fastembed/ONNX** – kein
  torch, kein API-Call, läuft auf der CPU. Das Modell (~1 GB) wird **einmalig** nach
  `EMBEDDING_CACHE_DIR` (`/data/models`, persistentes PVC) geladen.
- **Speicher:** `DocumentChunk.embedding` als **pgvector**-Spalte (Postgres-Image
  `pgvector/pgvector:pg16`). Ähnlichkeit = Cosine-Distance **in der Datenbank**.
- **Ein einziger Indexierungs-Pfad:** `pipeline.process_version()` ruft
  `semantic_index.sync_document_embeddings()` synchron nach dem OCR. Der Task
  `embed_document_version` delegiert an denselben Kern (nur für Backfill).
- **In Tests standardmäßig AUS** (`EMBEDDING_ENABLED` wird bei `manage.py test`
  auf `False` gesetzt), damit Pipeline-Tests kein 1-GB-Modell laden.

## Stellschrauben (ConfigMap `dms-config`)

| Variable | Default (Code) | Aktuell gesetzt | Bedeutung |
|---|---|---|---|
| `EMBEDDING_ENABLED` | `true` | – | Embedding-Erzeugung an/aus |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-large` | – | muss zu `EMBEDDING_DIM` passen |
| `EMBEDDING_DIM` | `1024` | – | Dimension (⚠️ Änderung = neue Migration des VectorField) |
| `EMBEDDING_CACHE_DIR` | `/data/models` | – | Modell-Cache (persistent!) |
| `EMBEDDING_MIN_SIMILARITY` | `0.70` | **`0.75`** | Floor für die Bedeutungssuche |
| `DUPLICATE_THRESHOLD` | `0.93` | **`0.975`** | ab hier „mögliche Version" |
| `DUPLICATE_STRONG_THRESHOLD` | `0.97` | **`0.980`** | ab hier „Duplikat" (zzgl. Lexik) |
| `DUPLICATE_LEXICAL_STRONG` | `0.80` | – | nötige Wort-Überlappung für „Duplikat" |
| `AUTO_FILE_ENABLED` | `false` | – | Autopilot **beim Ingest** (Opt-in!) |
| `AUTO_FILE_MIN_CONFIDENCE` | `0.75` | `0.75` | Stimmenanteil, ab dem der Autopilot zugreift |

> **Warum die Defaults ersetzt wurden:** siehe „Kalibrierung" – e5 komprimiert diesen
> (rechnungslastigen) Korpus in ein sehr enges, hohes Band. Die Code-Defaults sind für
> eine breite 0–1-Verteilung gedacht und hier viel zu niedrig.

## Management-Commands

```bash
# Backfill: Embeddings für den Bestand erzeugen (asynchron über die Queue)
python manage.py embed_documents            # aktuelle Version je Dokument
python manage.py embed_documents --all      # alle Versionen mit OCR-Text

# Reindex (synchron, mit Filtern)
python manage.py reindex_embeddings [--all] [--document-id N] [--limit N] [--dry-run]

# Betriebsstatus des Index
python manage.py embedding_health

# Schwellen kalibrieren (read-only)
python manage.py calibrate_embeddings [--owner USER] [--limit N] [--examples 5]
```

## Runbook: Kalibrierung

Die Schwellen **müssen** an den echten Bestand angepasst werden – Defaults sind Raten.

```bash
kubectl -n dms exec deploy/backend -- python manage.py calibrate_embeddings --examples 5
```

Das Command zeigt Histogramm, Perzentile und **Beispiel-Paare je Ähnlichkeitsband**.
Vorgehen: Beispiel-Paare von oben nach unten prüfen und

- `DUPLICATE_STRONG_THRESHOLD` dorthin setzen, wo Paare wirklich **dasselbe** Dokument sind,
- `DUPLICATE_THRESHOLD` dorthin, wo sie noch **klar verwandt** sind,
- `EMBEDDING_MIN_SIMILARITY` eher **recall-freundlich** wählen (die Hybrid-Suche liefert
  Präzision über den Volltext-Teil).

**Messung 2026-07 (61 Dokumente):**

```
Nachbar-Ähnlichkeit: p50=0.968  p75=0.971  p95=0.974  p99=0.980  min=0.880  max=0.980
Histogramm: 0.97–0.99 = 30 %, 0.95–0.97 = 55 %, 0.90–0.93 = 9 %, 0.85–0.90 = 5 %
```

Selbst **inhaltlich fremde** Dokumente liegen hier bei ~0.90. Bei `DUPLICATE_THRESHOLD=0.93`
(Default) hätte damit praktisch **jedes** Dokument einen „Duplikat"-Kandidaten.

## Bekannte Grenzen

- **Vorlagenlastige Korpora:** Ein echter Doppel-Scan („Honorarnote 2025-94 ↔ 2025-94")
  und zwei **verschiedene** Honorarnoten desselben Anbieters liegen **im selben
  Cosine-Band (~0.97)**. Embeddings allein trennen das nicht. Deshalb gilt „Duplikat"
  nur bei hohem Cosine **UND** hoher Wort-Überlappung (`DUPLICATE_LEXICAL_STRONG`);
  alles andere ist „mögliche Version".
- **Copilot-Agent** braucht einen konfigurierten LLM-Provider (`ANTHROPIC_API_KEY` im
  Secret, `AI_PROVIDER=anthropic`). Ohne Key liefert `/agent/plan/` sauber
  `status: "unavailable"` – der Rest des DMS bleibt unberührt.
- **Autopilot beim Ingest** ist bewusst **Opt-in** (`AUTO_FILE_ENABLED=false`), weil er
  Nutzerdaten ändert. Die manuelle Batch-Aktion läuft unabhängig davon.

## Sicherheitsmodell (kurz)

- **Schreiben ist immer owner-only.** `DocumentViewSet.get_queryset()` erweitert die
  Sichtbarkeit **nur** für eine Whitelist lesender Actions (`SAFE_READ_ACTIONS`) auf
  haushaltsgeteilte Fremd-Dokumente – **fail-closed**: eine vergessene Action ist zu
  streng, nie zu offen.
- **Familien-Freigabe:** Sichtbarkeit hängt an der Haushalts-Mitgliedschaft des
  **Eigentümers** (`_household_visibility_q()` – EINE Quelle für Liste, Detail, Copilot
  und Timeline). Ordner-Freigabe vererbt auf Unterordner.
- **Agent:** Die KI **schlägt nur vor** (Whitelist, nur zuvor ermittelte Kandidaten-IDs,
  strikte Validierung). Ausgeführt wird erst nach Bestätigung – deterministisch, ohne
  LLM, owner-gescoped, mit Audit. Jede Aktion ist **rückgängig machbar**
  (`POST /agent/undo/`).
