import { useEffect, useState } from "react";

import {
  getDocumentDuplicates,
  supersedeDocument,
  type DuplicateHit,
  type DuplicatesResult,
} from "../../api";

/**
 * Zeigt inhaltliche Beinah-Duplikate/Versionen dieses Dokuments (Cosine über die
 * Embeddings). „Duplikat" = praktisch derselbe Beleg (Re-Scan), „Version" = sehr
 * ähnlich. Mit Schreibrecht lässt sich ein Treffer per Klick als Dublette dieses
 * Dokuments ausblenden (Soft-Merge, umkehrbar).
 */
export function DuplicatesPanel({
  documentId,
  canEdit,
  onOpenDocument,
  onChanged,
}: {
  documentId: number;
  canEdit: boolean;
  onOpenDocument: (documentId: number, page?: number | null) => void;
  onChanged?: () => void;
}) {
  const [data, setData] = useState<DuplicatesResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mergingId, setMergingId] = useState<number | null>(null);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      setData(await getDocumentDuplicates(documentId));
    } catch {
      setError("Dubletten konnten nicht geladen werden.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId]);

  async function merge(hit: DuplicateHit) {
    setMergingId(hit.document);
    setError(null);
    try {
      // Der Treffer (hit) wird als Dublette DIESES Dokuments markiert/ausgeblendet.
      await supersedeDocument(hit.document, documentId);
      await load();
      onChanged?.();
    } catch {
      setError("Zusammenführen fehlgeschlagen.");
    } finally {
      setMergingId(null);
    }
  }

  return (
    <section className="card duplicates">
      <div className="duplicates__head">
        <h3>Mögliche Dubletten</h3>
        <p className="muted duplicates__hint">
          Inhaltlich (fast) gleiche Dokumente – öffnen zum Vergleichen, oder als
          Dublette dieses Dokuments ausblenden.
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
            <li key={hit.document} className="duplicates__row">
              <div className="duplicates__main">
                <span className={`duplicates__badge duplicates__badge--${hit.kind}`}>
                  {hit.kind === "duplicate" ? "Duplikat" : "Mögliche Version"}
                </span>
                <button className="link duplicates__title" onClick={() => onOpenDocument(hit.document)}>
                  {hit.title}
                </button>
              </div>
              <div className="duplicates__footer">
                <span className="duplicates__meta muted">
                  {Math.round(hit.score * 100)} % ähnlich
                  {hit.added_at
                    ? ` · ${new Date(hit.added_at).toLocaleDateString("de-AT")}`
                    : ""}
                </span>
                {canEdit && (
                  <button
                    className="link"
                    onClick={() => merge(hit)}
                    disabled={mergingId === hit.document}
                    title="Diesen Treffer als Dublette dieses Dokuments ausblenden (umkehrbar)"
                  >
                    {mergingId === hit.document ? "Führe zusammen …" : "Als Dublette ausblenden"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
