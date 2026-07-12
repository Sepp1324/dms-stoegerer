import { useEffect, useState } from "react";

import {
  applyFiling,
  getFilingSuggestions,
  type DocumentDetail,
  type FilingSuggestions,
} from "../../api";

/**
 * Auto-Ablage: schlägt Ordner/Tags/Korrespondent/Typ aus den inhaltlich
 * ähnlichsten Dokumenten vor (kNN über die lokalen Embeddings – kein LLM/Key).
 * „Übernehmen" füllt leere Felder und ergänzt Tags; manuelle Werte bleiben.
 */
export function AutoFilePanel({
  documentId,
  canEdit,
  onApplied,
}: {
  documentId: number;
  canEdit: boolean;
  onApplied: (doc: DocumentDetail) => void;
}) {
  const [data, setData] = useState<FilingSuggestions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      setData(await getFilingSuggestions(documentId));
    } catch {
      setError("Vorschläge konnten nicht geladen werden.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId]);

  async function apply() {
    setApplying(true);
    setError(null);
    setNote(null);
    try {
      const res = await applyFiling(documentId);
      onApplied(res.document);
      setNote(
        res.applied.length
          ? `Übernommen: ${res.applied.map(fieldLabel).join(", ")}.`
          : "Nichts zu übernehmen – alle Felder sind bereits gesetzt.",
      );
      await load();
    } catch {
      setError("Übernehmen fehlgeschlagen.");
    } finally {
      setApplying(false);
    }
  }

  const rows = data ? buildRows(data) : [];
  const hasNew = rows.some((r) => r.isNew);

  return (
    <section className="card auto-file">
      <div className="auto-file__head">
        <div>
          <h3>Auto-Ablage</h3>
          <p className="muted auto-file__hint">
            Vorschlag aus den inhaltlich ähnlichsten Dokumenten – lokal, ohne KI-Key.
          </p>
        </div>
        {canEdit && data?.status === "ok" && (
          <button onClick={apply} disabled={applying || !hasNew}>
            {applying ? "Übernehme …" : "Leere Felder übernehmen"}
          </button>
        )}
      </div>

      {busy && <p className="muted">Analysiere ähnliche Dokumente …</p>}
      {error && <p className="form-error">{error}</p>}
      {note && <p className="muted auto-file__note">{note}</p>}

      {data && data.status !== "ok" && !busy && (
        <p className="muted">{statusHint(data.status)}</p>
      )}

      {data?.status === "ok" && (
        <>
          <ul className="auto-file__rows">
            {rows.length === 0 && (
              <li className="muted">
                Keine belastbaren Vorschläge (zu wenig ähnliche Dokumente).
              </li>
            )}
            {rows.map((row) => (
              <li key={row.key} className="auto-file__row">
                <span className="auto-file__label">{row.label}</span>
                <span className="auto-file__value">
                  {row.value}
                  {row.isNew ? (
                    <span className="auto-file__conf">{row.confidence}</span>
                  ) : (
                    <span className="auto-file__current">bereits gesetzt</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          {!!data.neighbors?.length && (
            <p className="muted auto-file__basis">
              Basierend auf {data.neighbors.length} ähnlichen Dokumenten.
            </p>
          )}
        </>
      )}
    </section>
  );
}

type Row = {
  key: string;
  label: string;
  value: string;
  confidence: string;
  isNew: boolean;
};

function buildRows(data: FilingSuggestions): Row[] {
  const rows: Row[] = [];
  const cur = data.current;
  const pct = (c: number) => `${Math.round(c * 100)} %`;

  if (data.folder) {
    rows.push({
      key: "folder",
      label: "Ordner",
      value: data.folder.label,
      confidence: pct(data.folder.confidence),
      isNew: !cur?.folder,
    });
  }
  if (data.correspondent) {
    rows.push({
      key: "correspondent",
      label: "Korrespondent",
      value: data.correspondent.label,
      confidence: pct(data.correspondent.confidence),
      isNew: !cur?.correspondent,
    });
  }
  if (data.document_type) {
    rows.push({
      key: "document_type",
      label: "Dokumenttyp",
      value: data.document_type.label,
      confidence: pct(data.document_type.confidence),
      isNew: !cur?.document_type,
    });
  }
  const currentTags = new Set(cur?.tags ?? []);
  (data.tags ?? []).forEach((tag) => {
    rows.push({
      key: `tag-${tag.id}`,
      label: "Tag",
      value: tag.name,
      confidence: pct(tag.confidence),
      isNew: !currentTags.has(tag.id),
    });
  });
  return rows;
}

function fieldLabel(field: string): string {
  if (field === "folder") return "Ordner";
  if (field === "correspondent") return "Korrespondent";
  if (field === "document_type") return "Dokumenttyp";
  if (field === "tags") return "Tags";
  return field;
}

function statusHint(status: FilingSuggestions["status"]): string {
  if (status === "disabled") return "Der semantische Index ist deaktiviert.";
  if (status === "no_embeddings")
    return "Für dieses Dokument gibt es noch keine Embeddings (Reindex nötig).";
  return "Zu wenige ähnliche Dokumente für einen Vorschlag.";
}
