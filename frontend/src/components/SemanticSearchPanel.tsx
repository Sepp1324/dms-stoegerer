import { useEffect, useRef, useState } from "react";

import { semanticSearch, type SemanticSearchHit } from "../api";
import { sanitizeSnippet } from "../sanitize";

/**
 * Bedeutungssuche (pgvector/e5): findet Dokumente nach *Sinn*, nicht nur nach
 * exakten Wörtern. Owner-gescoped über die API. Bewusst als eigenes Overlay
 * gehalten, damit die klassische Volltextliste unberührt bleibt.
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
  const [hits, setHits] = useState<SemanticSearchHit[] | null>(null);
  const [disabled, setDisabled] = useState(false);
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
      const res = await semanticSearch(trimmed, 10);
      setHits(res.results);
      setDisabled(!res.enabled);
    } catch {
      setError("Bedeutungssuche fehlgeschlagen. Bitte erneut versuchen.");
    } finally {
      setLoading(false);
    }
  }

  // Beim Öffnen fokussieren und – falls schon eine Anfrage anliegt – direkt suchen.
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
      aria-label="Bedeutungssuche"
      onClick={onClose}
    >
      <div
        className="modal-card semantic-search"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-card__head">
          <h3>Bedeutungssuche</h3>
          <button className="link" onClick={onClose} aria-label="Schließen">
            ✕
          </button>
        </div>

        <p className="muted semantic-search__hint">
          Findet Dokumente nach Sinn – z. B. „Wann läuft meine Kfz-Versicherung
          aus?" trifft auch Polizzen ohne diese Wörter.
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
        {disabled && (
          <p className="muted">
            Der semantische Index ist deaktiviert (EMBEDDING_ENABLED=false).
          </p>
        )}

        <div className="semantic-search__results">
          {hits && hits.length === 0 && !loading && (
            <p className="muted">Keine bedeutungsähnlichen Treffer.</p>
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
                <span className="semantic-hit__score" title="Ähnlichkeit">
                  {Math.round(hit.score * 100)} %
                </span>
              </div>
              <p className="muted semantic-hit__meta">
                {hit.folder_path ?? "Kein Ordner"}
              </p>
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
