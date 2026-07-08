import { useEffect, useMemo, useState } from "react";
import {
  getPdfWorkbenchPages,
  mergePdfDocuments,
  rewritePdfDocument,
  splitPdfDocument,
  type PdfWorkbenchManifest,
  type PdfWorkbenchPageSpec,
  type PdfWorkbenchSplitPart,
} from "../../api";

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

function parseRewritePlan(value: string): PdfWorkbenchPageSpec[] {
  const specs: PdfWorkbenchPageSpec[] = [];
  for (const raw of value.split(",")) {
    const item = raw.trim();
    if (!item) continue;
    const match = item.match(/^(\d+)(?:r(90|180|270))?$/i);
    if (!match) throw new Error(`Ungültiger Eintrag: ${item}`);
    specs.push({
      page: Number(match[1]),
      rotation: match[2] ? (Number(match[2]) as 90 | 180 | 270) : 0,
    });
  }
  if (!specs.length) throw new Error("Mindestens eine Seite ist erforderlich.");
  return specs;
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

function defaultPlan(manifest: PdfWorkbenchManifest | null): string {
  if (!manifest) return "";
  return manifest.pages.map((page) => String(page.page)).join(",");
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [rewritePlan, setRewritePlan] = useState("");
  const [splitPlan, setSplitPlan] = useState("");
  const [mergeIds, setMergeIds] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  const pageLabel = useMemo(() => {
    if (!manifest) return "Keine Seiten geladen";
    return `${manifest.page_count} Seiten · Version ${manifest.version_no}`;
  }, [manifest]);

  function load() {
    setLoading(true);
    setError(null);
    getPdfWorkbenchPages(documentId)
      .then((data) => {
        setManifest(data);
        setRewritePlan(defaultPlan(data));
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(load, [documentId]);

  async function runRewrite() {
    setBusy("rewrite");
    setError(null);
    setMessage(null);
    try {
      const specs = parseRewritePlan(rewritePlan);
      await rewritePdfDocument(documentId, specs, "PDF-Werkbank");
      setMessage("Neue Version wurde erstellt und zur Verarbeitung eingereiht.");
      onChanged();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function runSplit() {
    setBusy("split");
    setError(null);
    setMessage(null);
    try {
      const parts = parseSplitPlan(splitPlan);
      const result = await splitPdfDocument(documentId, parts);
      setMessage(`${result.documents.length} neue Dokumente wurden erstellt.`);
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
      const ids = mergeIds
        .split(",")
        .map((item) => Number(item.trim()))
        .filter((id) => Number.isInteger(id) && id > 0);
      if (!ids.length) throw new Error("Mindestens eine Dokument-ID ist erforderlich.");
      await mergePdfDocuments(documentId, ids, "PDF-Werkbank");
      setMessage("Zusammenführung wurde als neue Version erstellt.");
      setMergeIds("");
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

      {manifest && (
        <div className="pdf-page-strip" aria-label="PDF-Seiten">
          {manifest.pages.map((page) => (
            <span key={page.page}>
              {page.page}
              {page.rotation ? ` · ${page.rotation}°` : ""}
            </span>
          ))}
        </div>
      )}

      <section className="pdf-workbench__tool">
        <h4>Seiten neu schreiben</h4>
        <p className="muted">
          Reihenfolge per Komma. Weggelassene Seiten werden entfernt, Rotation mit
          r90/r180/r270.
        </p>
        <textarea
          value={rewritePlan}
          onChange={(event) => setRewritePlan(event.target.value)}
          disabled={!canEdit}
          rows={3}
          placeholder="1,2,3 oder 3,1r90,2"
        />
        <button type="button" onClick={runRewrite} disabled={!canEdit || busy === "rewrite"}>
          {busy === "rewrite" ? "Erstelle Version ..." : "Als neue Version speichern"}
        </button>
      </section>

      <section className="pdf-workbench__tool">
        <h4>Dokument splitten</h4>
        <p className="muted">Eine Zeile pro neues Dokument: Titel: Seiten oder Bereiche.</p>
        <textarea
          value={splitPlan}
          onChange={(event) => setSplitPlan(event.target.value)}
          disabled={!canEdit}
          rows={4}
          placeholder={"Anschreiben: 1-2\nBeilage: 3,4"}
        />
        <button type="button" onClick={runSplit} disabled={!canEdit || busy === "split"}>
          {busy === "split" ? "Splitte ..." : "Neue Dokumente erzeugen"}
        </button>
      </section>

      <section className="pdf-workbench__tool">
        <h4>Dokumente zusammenführen</h4>
        <p className="muted">
          Kommagetrennte Dokument-IDs werden hinter dieses Dokument gehängt.
        </p>
        <input
          value={mergeIds}
          onChange={(event) => setMergeIds(event.target.value)}
          disabled={!canEdit}
          placeholder="123,124"
        />
        <button type="button" onClick={runMerge} disabled={!canEdit || busy === "merge"}>
          {busy === "merge" ? "Führe zusammen ..." : "Als neue Version zusammenführen"}
        </button>
      </section>
    </div>
  );
}
