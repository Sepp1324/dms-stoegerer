# Embedding-/Pillow-Upgrade — Runbook

> **STATUS: Semantische Suche DEAKTIVIERT (`EMBEDDING_ENABLED=false`), FTS-only.**
> Nachlese der ganzen Kaskade: Das 0.8.0/Pillow-12-Upgrade wurde zurückgerollt
> (fastembed 0.8.0 lud das Modell via hf-xet nicht, dann OOM). Aber auch mit 0.3.6
> OOMt das Laden von e5-large (2,2 GB) + onnxruntime 1.27 (Graph-Optimierungs-Spike)
> **selbst mit 8Gi** – die Node hat zwar ~11Gi frei, der Ladespike ist aber für
> diesen Cluster nicht tragbar. Daher: Embeddings aus, DMS läuft voll auf der
> Postgres-Volltextsuche (inkl. GIN-Index).
>
> **Wiederbelebung als eigenes Projekt** (nicht am Prod-Cluster erzwingen):
> kleines Modell `intfloat/multilingual-e5-small` (384-dim → `DocumentChunk.embedding`-
> VectorField-Migration 1024→384 + Neukalibrierung) **oder** onnxruntime-Graph-
> Optimierung deaktivieren (braucht fastembed-Support). Vorher in einer Staging-
> Umgebung testen. Das Dokument bleibt als Historie/Anleitung erhalten.

Kontext: `fastembed 0.3.6 → 0.8.0` (hebt die `pillow<11`-Decke auf) + `Pillow 10.4.0 → 12.3.0` (schließt offene Pillow-CVEs) + `pillow-heif 0.21.0`. Der fastembed-Sprung ist groß (numpy 2.x + onnxruntime 1.27) — Schritt 2 (Neu-Einbetten) ist damit **Pflicht**, nicht optional.

**Modell wird ins Image gebacken.** Der erste Anlauf (#242) scheiterte, weil fastembed 0.8.0 das ONNX-Modell zur Laufzeit über hf-xet lud und das im Cluster fehlschlug. Jetzt lädt der **Deploy-Build** das Modell (mit `HF_HUB_DISABLE_XET=1`) nach `/opt/models` ins Image (`backend/Dockerfile` + `ci/bake_model.py`), und `EMBEDDING_CACHE_DIR=/opt/models` zeigt darauf. Zur Laufzeit gibt es **keinen Download** mehr. Kann der Runner das Modell nicht laden, schlägt der Build fehl → **kein Deploy** (das laufende Image bleibt).

Der Code nutzt nur Basis-Pillow-APIs → **kein Code-Fix nötig**. Das Modell bleibt
`intfloat/multilingual-e5-large` (1024-dim). **Aber:** Die konkreten Embedding-Werte
können sich mit der fastembed-/ONNX-Version minimal ändern. Die CI verifiziert nur,
dass alles **baut und importiert** — **nicht**, ob die Embeddings identisch sind.
Deshalb dieser Nachlauf.

> Ein Merge nach `main` deployt automatisch. Die folgenden Schritte laufen **nach**
> dem Deploy im Cluster (`kubectl -n dms exec deploy/backend -- …`).

## 1. Sanity-Check (Modell lädt, Dimension stimmt)

```bash
kubectl -n dms exec deploy/backend -- python manage.py embedding_health
```
Erwartung: Modell lädt (aus dem gebackenen `/opt/models`, **kein** Download),
Dimension **1024**.

## 2. Bestand neu einbetten (Konsistenz herstellen)

Falls sich die Werte geändert haben, wären **alt** (0.3.6) und **neu** (0.5.1)
eingebettete Chunks im selben pgvector-Raum inkonsistent → Suche/Dubletten
degradieren. Daher den gesamten Bestand einmal mit der neuen Version neu einbetten:

```bash
kubectl -n dms exec deploy/backend -- python manage.py reindex_embeddings --all
```
(Für den Familien-Korpus Minuten; einmalig.)

> **Speicher:** Das Einbetten lädt das e5-large-Modell (~2,5 GB). Der backend-Pod
> hat dafür jetzt 3Gi (früher 1Gi → OOMKill/exit 137). Alternativ im worker-Pod
> laufen lassen (`kubectl -n dms exec deploy/worker -- …`, 4Gi). Das gebackene
> Modell gehört dem Runtime-User (uid 1000), damit fastembed seinen Cache schreiben
> kann.

## 3. Neu kalibrieren

```bash
kubectl -n dms exec deploy/backend -- python manage.py calibrate_embeddings
```
Zeigt Histogramm/Perzentile der Nachbar-Ähnlichkeiten. Mit den **alten**
Kalibrierdaten vergleichen (p50 lag zuvor ~0.968, min ~0.88). Verschiebt sich die
Verteilung spürbar, die Schwellen per Env am Backend-Deployment nachziehen:

- `DUPLICATE_THRESHOLD` (Default 0.93)
- `DUPLICATE_STRONG_THRESHOLD` (Default 0.97)
- `DUPLICATE_LEXICAL_STRONG` (Default 0.80)

## 4. Stichprobe

Ein, zwei bekannte Doppel-Scans hochladen und prüfen, dass die Dubletten-Erkennung
noch greift; eine Handvoll Suchen gegenchecken.

## Rollback

Rein image-seitig: Den Pin-Bump in `backend/requirements.txt` reverten (PR
zurückrollen) → alter Deploy. Danach ggf. erneut `reindex_embeddings --all`, damit
der Bestand wieder mit der alten Version konsistent ist.
