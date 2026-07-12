import { useEffect, useState } from "react";

import {
  getDocumentDuplicates,
  type DuplicateHit,
  type DuplicatesResult,
} from "../../api";

/**
 * Zeigt inhaltliche Beinah-Duplikate/Versionen dieses Dokuments (Cosine über die
 * Embeddings). „Duplikat" = praktisch derselbe Beleg (Re-Scan), „Version" = sehr
 * ähnlich (evtl. neuere Fassung). Zum Vergleichen/Aufräumen öffnen.
 */
export function DuplicatesPanel({
  documentId,
  onOpenDocument,
}: {
  documentId: number;
  onOpenDocument: (documentId: number, page?: number | null) => void;
}) {
  const [data, setData] = useState<DuplicatesResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    setBusy(true);
    setError(null);
    getDocumentDuplicates(documentId)
      .then((res) => active && setData(res))
      .catch(() => active && setError("Dubletten konnten nicht geladen werden."))
      .finally(() => active && setBusy(false));
    return () => {
      active = false;
    };
  }, [documentId]);

  return (
    <section className="card duplicates">
      <div className="duplicates__head">
        <h3>Mögliche Dubletten</h3>
        <p className="muted duplicates__hint">
          Inhaltlich (fast) gleiche Dokumente – zum Vergleichen/Aufräumen öffnen.
        </p>
      </div>

      {busy && <p className="muted">Prüfe auf Dubletten …</p>}
      {error && <p className="form-error">{error}</p>}
      {data && data.status === "no_embeddings" && !busy && (
        <p className="muted">Noch keine Embeddings für dieses Dokument (Reindex nötig).</p>
      )}
      {data && data.status === "disabled" && !busy && (
        <p className="muted">Der semantische Index ist deaktiviert.</p>
      )}
      {data?.status === "ok" && data.results.length === 0 && !busy && (
        <p className="muted">Keine Dubletten gefunden. 🎉</p>
      )}

      {data?.status === "ok" && data.results.length > 0 && (
        <ul className="duplicates__list">
          {data.results.map((hit) => (
            <DuplicateRow key={hit.document} hit={hit} onOpen={() => onOpenDocument(hit.document)} />
          ))}
        </ul>
      )}
    </section>
  );
}

function DuplicateRow({ hit, onOpen }: { hit: DuplicateHit; onOpen: () => void }) {
  const isDup = hit.kind === "duplicate";
  return (
    <li className="duplicates__row">
      <div className="duplicates__main">
        <span className={`duplicates__badge duplicates__badge--${hit.kind}`}>
          {isDup ? "Duplikat" : "Mögliche Version"}
        </span>
        <button className="link duplicates__title" onClick={onOpen}>
          {hit.title}
        </button>
      </div>
      <div className="duplicates__meta muted">
        {Math.round(hit.score * 100)} % ähnlich
        {hit.added_at ? ` · ${new Date(hit.added_at).toLocaleDateString("de-AT")}` : ""}
      </div>
    </li>
  );
}
