import { useEffect, useRef, useState } from "react";

import { hybridSearch, type HybridSearchHit } from "../api";
import { sanitizeSnippet } from "../sanitize";

function sourceLabel(sources: HybridSearchHit["sources"]): string {
  const hasFts = sources.includes("fts");
  const hasSem = sources.includes("semantic");
  if (hasFts && hasSem) return "Volltext + Bedeutung";
  if (hasSem) return "Bedeutung";
  return "Volltext";
}

/**
 * Smart-Suche: fusioniert Volltext (exakte Begriffe) und semantische Bedeutung zu
 * einem Ranking (RRF). Bewusst als eigenes Overlay, damit die klassische
 * Volltextliste unberührt bleibt.
 */
export default function SemanticSearchPanel({
  initialQuery,
  onClose,
  onOpenDocument,
}: {
  initialQuery: string;
  onClose: () => void;
  onOpenDocument: (documentId: number) => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hits, setHits] = useState<HybridSearchHit[] | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function run(q: string) {
    const trimmed = q.trim();
    if (trimmed.length < 3) {
      setError("Bitte mindestens 3 Zeichen eingeben.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await hybridSearch(trimmed, 12);
      setHits(res.results);
    } catch {
      setError("Suche fehlgeschlagen. Bitte erneut versuchen.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    inputRef.current?.focus();
    if (initialQuery.trim().length >= 3) {
      void run(initialQuery);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Smart-Suche"
      onClick={onClose}
    >
      <div className="modal-card semantic-search" onClick={(e) => e.stopPropagation()}>
        <div className="modal-card__head">
          <h3>Smart-Suche</h3>
          <button className="link" onClick={onClose} aria-label="Schließen">
            ✕
          </button>
        </div>

        <p className="muted semantic-search__hint">
          Kombiniert exakte Volltext-Treffer mit der Bedeutung – z. B. „Wann läuft
          meine Kfz-Versicherung aus?" findet auch Polizzen ohne diese Wörter.
        </p>

        <form
          className="semantic-search__form"
          onSubmit={(e) => {
            e.preventDefault();
            void run(query);
          }}
        >
          <input
            ref={inputRef}
            className="search"
            placeholder="Frage oder Thema …"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="submit" disabled={loading}>
            {loading ? "Suche …" : "Suchen"}
          </button>
        </form>

        {error && <p className="form-error">{error}</p>}

        <div className="semantic-search__results">
          {hits && hits.length === 0 && !loading && (
            <p className="muted">Keine Treffer.</p>
          )}
          {hits?.map((hit) => (
            <article key={hit.document} className="card semantic-hit">
              <div className="semantic-hit__head">
                <button
                  className="link semantic-hit__title"
                  onClick={() => onOpenDocument(hit.document)}
                >
                  {hit.document_title}
                </button>
                <span className="semantic-hit__score" title="Fundstelle">
                  {sourceLabel(hit.sources)}
                </span>
              </div>
              <p className="muted semantic-hit__meta">{hit.folder_path ?? "Kein Ordner"}</p>
              {(hit.snippet_html || hit.snippet) && (
                <p
                  className="semantic-hit__snippet"
                  dangerouslySetInnerHTML={{
                    __html: sanitizeSnippet(hit.snippet_html || hit.snippet),
                  }}
                />
              )}
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}
