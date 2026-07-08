import { useEffect, useMemo, useState } from "react";
import {
  getDocuments,
  getPdfWorkbenchPageThumbnail,
  getPdfWorkbenchPages,
  mergePdfDocuments,
  rewritePdfDocument,
  splitPdfDocument,
  type DocumentItem,
  type PdfWorkbenchManifest,
  type PdfWorkbenchPage,
  type PdfWorkbenchPageSpec,
  type PdfWorkbenchSplitPart,
} from "../../api";

type PageItem = PdfWorkbenchPage & {
  key: string;
  rotationDelta: 0 | 90 | 180 | 270;
};

function toItems(manifest: PdfWorkbenchManifest | null): PageItem[] {
  if (!manifest) return [];
  return manifest.pages.map((page) => ({
    ...page,
    key: `${manifest.version_id}-${page.page}`,
    rotationDelta: 0,
  }));
}

function parsePages(value: string): number[] {
  const pages: number[] = [];
  for (const raw of value.split(",")) {
    const item = raw.trim();
    if (!item) continue;
    const range = item.match(/^(\d+)\s*-\s*(\d+)$/);
    if (range) {
      const start = Number(range[1]);
      const end = Number(range[2]);
      if (start < 1 || end < start) throw new Error(`Ungültiger Bereich: ${item}`);
      for (let page = start; page <= end; page += 1) pages.push(page);
      continue;
    }
    const page = Number(item);
    if (!Number.isInteger(page) || page < 1) throw new Error(`Ungültige Seite: ${item}`);
    pages.push(page);
  }
  return pages;
}

function parseSplitPlan(value: string): PdfWorkbenchSplitPart[] {
  const parts = value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, idx) => {
      const [titlePart, pagesPart] = line.includes(":")
        ? line.split(/:(.+)/).filter(Boolean)
        : [`Teil ${idx + 1}`, line];
      const title = titlePart.trim() || `Teil ${idx + 1}`;
      const pages = parsePages((pagesPart ?? "").trim());
      if (!pages.length) throw new Error(`Teil "${title}" hat keine Seiten.`);
      return { title, pages };
    });
  if (!parts.length) throw new Error("Mindestens ein Split-Teil ist erforderlich.");
  return parts;
}

function specsFromItems(items: PageItem[]): PdfWorkbenchPageSpec[] {
  return items.map((item) => ({
    page: item.page,
    rotation: item.rotationDelta,
  }));
}

function selectedPages(items: PageItem[], selected: Set<string>): number[] {
  return items.filter((item) => selected.has(item.key)).map((item) => item.page);
}

function rotateDelta(delta: PageItem["rotationDelta"]): PageItem["rotationDelta"] {
  return ((delta + 90) % 360) as PageItem["rotationDelta"];
}

function totalRotation(item: PageItem): number {
  return (item.rotation + item.rotationDelta) % 360;
}

function PageThumb({
  documentId,
  item,
}: {
  documentId: number;
  item: PageItem;
}) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let url: string | null = null;
    setSrc(null);
    getPdfWorkbenchPageThumbnail(documentId, item.page)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => {
        /* Fallback unten rendert Seitenzahl; Miniatur ist Komfort. */
      });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [documentId, item.page]);

  return (
    <div className="pdf-page-card__thumb">
      {src ? (
        <img
          src={src}
          alt=""
          style={{ transform: `rotate(${totalRotation(item)}deg)` }}
        />
      ) : (
        <span>{item.page}</span>
      )}
    </div>
  );
}

export function PdfWorkbenchPanel({
  documentId,
  canEdit,
  onChanged,
}: {
  documentId: number;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const [manifest, setManifest] = useState<PdfWorkbenchManifest | null>(null);
  const [items, setItems] = useState<PageItem[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [splitTitle, setSplitTitle] = useState("");
  const [splitPlan, setSplitPlan] = useState("");
  const [mergeQuery, setMergeQuery] = useState("");
  const [mergeResults, setMergeResults] = useState<DocumentItem[]>([]);
  const [mergeIds, setMergeIds] = useState<number[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  const pageLabel = useMemo(() => {
    if (!manifest) return "Keine Seiten geladen";
    return `${items.length} von ${manifest.page_count} Seiten · Version ${manifest.version_no}`;
  }, [items.length, manifest]);
  const selectedCount = selected.size;
  const changed =
    manifest !== null &&
    (items.length !== manifest.pages.length ||
      items.some((item, idx) => {
        const original = manifest.pages[idx];
        return (
          original?.page !== item.page ||
          item.rotationDelta !== 0
        );
      }));

  function load() {
    setLoading(true);
    setError(null);
    getPdfWorkbenchPages(documentId)
      .then((data) => {
        setManifest(data);
        setItems(toItems(data));
        setSelected(new Set());
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(load, [documentId]);

  useEffect(() => {
    if (!mergeQuery.trim()) {
      setMergeResults([]);
      return;
    }
    let active = true;
    getDocuments({ q: mergeQuery.trim(), page: 1 })
      .then((res) => {
        if (!active) return;
        setMergeResults(
          res.results
            .filter((doc) => doc.id !== documentId && !mergeIds.includes(doc.id))
            .slice(0, 6),
        );
      })
      .catch(() => active && setMergeResults([]));
    return () => {
      active = false;
    };
  }, [documentId, mergeIds, mergeQuery]);

  function toggleSelected(key: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function rotatePage(key: string) {
    setItems((current) =>
      current.map((item) =>
        item.key === key ? { ...item, rotationDelta: rotateDelta(item.rotationDelta) } : item,
      ),
    );
  }

  function deletePage(key: string) {
    setItems((current) => current.filter((item) => item.key !== key));
    setSelected((current) => {
      const next = new Set(current);
      next.delete(key);
      return next;
    });
  }

  function deleteSelected() {
    setItems((current) => current.filter((item) => !selected.has(item.key)));
    setSelected(new Set());
  }

  function resetPlan() {
    setItems(toItems(manifest));
    setSelected(new Set());
    setMessage(null);
  }

  function onDrop(targetKey: string) {
    if (!dragKey || dragKey === targetKey) return;
    setItems((current) => {
      const from = current.findIndex((item) => item.key === dragKey);
      const to = current.findIndex((item) => item.key === targetKey);
      if (from < 0 || to < 0) return current;
      const next = [...current];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
    setDragKey(null);
  }

  async function runRewrite() {
    setBusy("rewrite");
    setError(null);
    setMessage(null);
    try {
      if (!items.length) throw new Error("Mindestens eine Seite ist erforderlich.");
      await rewritePdfDocument(documentId, specsFromItems(items), "PDF-Werkbank");
      setMessage("Neue Version wurde erstellt und zur Verarbeitung eingereiht.");
      onChanged();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function runSplitSelected() {
    setBusy("split-selected");
    setError(null);
    setMessage(null);
    try {
      const pages = selectedPages(items, selected);
      if (!pages.length) throw new Error("Bitte zuerst Seiten auswählen.");
      const title = splitTitle.trim() || `Auszug ${pages.join(",")}`;
      const result = await splitPdfDocument(documentId, [{ title, pages }]);
      setMessage(`${result.documents.length} neues Dokument wurde erstellt.`);
      setSplitTitle("");
      setSelected(new Set());
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function runSplitPlan() {
    setBusy("split-plan");
    setError(null);
    setMessage(null);
    try {
      const parts = parseSplitPlan(splitPlan);
      const result = await splitPdfDocument(documentId, parts);
      setMessage(`${result.documents.length} neue Dokumente wurden erstellt.`);
      setSplitPlan("");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function runMerge() {
    setBusy("merge");
    setError(null);
    setMessage(null);
    try {
      if (!mergeIds.length) throw new Error("Mindestens ein Dokument auswählen.");
      await mergePdfDocuments(documentId, mergeIds, "PDF-Werkbank");
      setMessage("Zusammenführung wurde als neue Version erstellt.");
      setMergeIds([]);
      setMergeQuery("");
      onChanged();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="pdf-workbench">
      <div className="pdf-workbench__head">
        <div>
          <strong>PDF-Werkbank</strong>
          <span className="muted">{loading ? "Lade Seiten ..." : pageLabel}</span>
        </div>
        <button type="button" className="link" onClick={load}>
          Aktualisieren
        </button>
      </div>

      {error && (
        <div className="state state--error">
          <strong>Werkbank-Aktion fehlgeschlagen.</strong>
          <span>{error}</span>
        </div>
      )}
      {message && (
        <div className="state">
          <strong>Aktion gestartet.</strong>
          <span>{message}</span>
        </div>
      )}

      <div className="pdf-workbench-toolbar">
        <button type="button" onClick={runRewrite} disabled={!canEdit || !changed || busy === "rewrite"}>
          {busy === "rewrite" ? "Speichere ..." : "Aus Plan neue Version"}
        </button>
        <button type="button" className="link" onClick={resetPlan} disabled={!canEdit || !changed}>
          Zurücksetzen
        </button>
        <button type="button" className="link" onClick={deleteSelected} disabled={!canEdit || selectedCount === 0}>
          Auswahl entfernen
        </button>
        <span className="muted">{selectedCount} ausgewählt</span>
      </div>

      <div className="pdf-page-grid" aria-label="PDF-Seiten">
        {items.map((item, index) => (
          <article
            key={item.key}
            className={`pdf-page-card${selected.has(item.key) ? " pdf-page-card--selected" : ""}`}
            draggable={canEdit}
            onDragStart={() => setDragKey(item.key)}
            onDragOver={(event) => event.preventDefault()}
            onDrop={() => onDrop(item.key)}
          >
            <label className="pdf-page-card__select">
              <input
                type="checkbox"
                checked={selected.has(item.key)}
                onChange={() => toggleSelected(item.key)}
                disabled={!canEdit}
              />
              Seite {item.page}
            </label>
            <PageThumb documentId={documentId} item={item} />
            <div className="pdf-page-card__meta">
              <span>#{index + 1}</span>
              <span>{totalRotation(item)}°</span>
            </div>
            <div className="pdf-page-card__actions">
              <button type="button" className="link" onClick={() => rotatePage(item.key)} disabled={!canEdit}>
                Drehen
              </button>
              <button type="button" className="link link--danger" onClick={() => deletePage(item.key)} disabled={!canEdit || items.length <= 1}>
                Löschen
              </button>
            </div>
          </article>
        ))}
      </div>

      <section className="pdf-workbench__tool">
        <h4>Aus Auswahl neues Dokument erstellen</h4>
        <p className="muted">Die ausgewählten Seiten werden in aktueller Reihenfolge kopiert.</p>
        <input
          value={splitTitle}
          onChange={(event) => setSplitTitle(event.target.value)}
          disabled={!canEdit}
          placeholder="Titel des neuen Dokuments"
        />
        <button type="button" onClick={runSplitSelected} disabled={!canEdit || selectedCount === 0 || busy === "split-selected"}>
          {busy === "split-selected" ? "Erstelle ..." : "Auswahl als Dokument"}
        </button>
      </section>

      <section className="pdf-workbench__tool">
        <h4>Stapel manuell splitten</h4>
        <p className="muted">Eine Zeile pro neues Dokument: Titel: Seiten oder Bereiche.</p>
        <textarea
          value={splitPlan}
          onChange={(event) => setSplitPlan(event.target.value)}
          disabled={!canEdit}
          rows={4}
          placeholder={"Anschreiben: 1-2\nBeilage: 3,4"}
        />
        <button type="button" onClick={runSplitPlan} disabled={!canEdit || busy === "split-plan"}>
          {busy === "split-plan" ? "Splitte ..." : "Neue Dokumente erzeugen"}
        </button>
      </section>

      <section className="pdf-workbench__tool">
        <h4>Dokumente zusammenführen</h4>
        <p className="muted">Suche Dokumente und hänge sie hinter dieses PDF.</p>
        <input
          value={mergeQuery}
          onChange={(event) => setMergeQuery(event.target.value)}
          disabled={!canEdit}
          placeholder="Dokument suchen"
        />
        {mergeResults.length > 0 && (
          <div className="pdf-merge-results">
            {mergeResults.map((doc) => (
              <button
                type="button"
                className="link"
                key={doc.id}
                onClick={() => {
                  setMergeIds((current) => [...current, doc.id]);
                  setMergeQuery("");
                }}
              >
                {doc.title}
              </button>
            ))}
          </div>
        )}
        {mergeIds.length > 0 && (
          <div className="pdf-merge-selected">
            {mergeIds.map((id) => (
              <span key={id}>
                #{id}
                <button
                  type="button"
                  className="link"
                  onClick={() => setMergeIds((current) => current.filter((item) => item !== id))}
                >
                  entfernen
                </button>
              </span>
            ))}
          </div>
        )}
        <button type="button" onClick={runMerge} disabled={!canEdit || mergeIds.length === 0 || busy === "merge"}>
          {busy === "merge" ? "Führe zusammen ..." : "Als neue Version zusammenführen"}
        </button>
      </section>
    </div>
  );
}
