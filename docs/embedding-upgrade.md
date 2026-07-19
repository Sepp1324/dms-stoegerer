# Embedding-/Pillow-Upgrade — Runbook

Kontext: `fastembed 0.3.6 → 0.5.1` (hebt die `pillow<11`-Decke auf) + `Pillow 10.4.0 → 11.1.0` (schließt offene Pillow-CVEs) + `pillow-heif 0.21.0`.

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
Erwartung: Modell lädt, Dimension **1024**. (Beim ersten Laden zieht fastembed das
ONNX-Modell ggf. neu in `EMBEDDING_CACHE_DIR` = `/data/models` — braucht kurz Netz.)

## 2. Bestand neu einbetten (Konsistenz herstellen)

Falls sich die Werte geändert haben, wären **alt** (0.3.6) und **neu** (0.5.1)
eingebettete Chunks im selben pgvector-Raum inkonsistent → Suche/Dubletten
degradieren. Daher den gesamten Bestand einmal mit der neuen Version neu einbetten:

```bash
kubectl -n dms exec deploy/backend -- python manage.py reindex_embeddings --all
```
(Für den Familien-Korpus Minuten; einmalig.)

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
