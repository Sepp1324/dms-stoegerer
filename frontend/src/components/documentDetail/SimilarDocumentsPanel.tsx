import { useEffect, useState } from "react";
import {
  getSimilarDocuments,
  reindexDocumentEmbeddings,
  type SimilarDocument,
  type SimilarDocumentsResult,
} from "../../api";
import { sanitizeSnippet } from "../../sanitize";

export function SimilarDocumentsPanel({
  documentId,
  canEdit,
  onOpenDocument,
}: {
  documentId: number;
  canEdit: boolean;
  onOpenDocument: (documentId: number, page?: number | null) => void;
}) {
  const [result, setResult] = useState<SimilarDocumentsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reindexing, setReindexing] = useState(false);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      setResult(await getSimilarDocuments(documentId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ähnliche Dokumente konnten nicht geladen werden.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId]);

  async function reindex() {
    setReindexing(true);
    setError(null);
    try {
      await reindexDocumentEmbeddings(documentId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Semantischer Index fehlgeschlagen.");
    } finally {
      setReindexing(false);
    }
  }

  return (
    <div className="similar-panel">
      <div className="similar-panel__head">
        <div>
          <h3>Ähnliche Dokumente</h3>
          <p className="muted">
            {result?.indexed
              ? `Semantischer Index: ${result.model}`
              : "Für dieses Dokument fehlt der semantische Index."}
          </p>
        </div>
        {canEdit && (
          <button onClick={reindex} disabled={reindexing}>
            {reindexing ? "Indexiere …" : "Neu indexieren"}
          </button>
        )}
      </div>

      {error && <p className="status status--error">{error}</p>}
      {busy && <p className="muted">Lade ähnliche Dokumente …</p>}

      {!busy && result && result.results.length === 0 && (
        <p className="muted">
          Noch keine ähnlichen Dokumente gefunden. Nach einem Backfill werden hier
          semantisch verwandte Dokumente angezeigt.
        </p>
      )}

      <div className="similar-list">
        {result?.results.map((item) => (
          <SimilarCard
            key={`${item.document}-${item.page ?? "doc"}`}
            item={item}
            onOpen={() => onOpenDocument(item.document, item.page)}
          />
        ))}
      </div>
    </div>
  );
}

function SimilarCard({
  item,
  onOpen,
}: {
  item: SimilarDocument;
  onOpen: () => void;
}) {
  return (
    <article className="similar-card">
      <div className="similar-card__head">
        <strong>{item.document_title}</strong>
        <button className="link" onClick={onOpen}>
          {item.page ? `Seite ${item.page} öffnen` : "Öffnen"}
        </button>
      </div>
      <p className="muted">
        {item.folder_path ?? "Kein Ordner"} · Score {(item.score * 100).toFixed(0)}%
      </p>
      <span className="similar-card__reason">{item.reason}</span>
      <p
        className="similar-card__snippet"
        dangerouslySetInnerHTML={{
          __html: sanitizeSnippet(item.snippet_html || item.snippet),
        }}
      />
    </article>
  );
}
