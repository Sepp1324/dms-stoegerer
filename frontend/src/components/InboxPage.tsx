import { useEffect, useState } from "react";
import {
  getDocuments,
  getDocumentThumbnail,
  markDocumentReviewed,
  type DocumentItem,
} from "../api";
import { ProcessingBadge } from "./ProcessingStatus";

function Thumb({ doc }: { doc: DocumentItem }) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let url: string | null = null;
    getDocumentThumbnail(doc.id)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => {
        /* Noch kein Thumbnail vorhanden: Icon-Fallback reicht für die Inbox. */
      });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [doc.id]);

  return (
    <div className="inbox-thumb" aria-hidden="true">
      {src ? (
        <img src={src} alt="" />
      ) : (
        <svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true">
          <path
            fill="currentColor"
            d="M6 2h7l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2m7 1.5V8h4.5z"
          />
        </svg>
      )}
    </div>
  );
}

export default function InboxPage({
  canEdit,
  onOpenDocument,
}: {
  canEdit: boolean;
  onOpenDocument: (id: number) => void;
}) {
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<number | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    getDocuments({
      review_status: "needs_review",
      processing_state: "ready",
      ordering: "-added_at",
    })
      .then((res) => {
        setDocs(res.results);
        setCount(res.count);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  async function markReviewed(docId: number) {
    setSavingId(docId);
    setError(null);
    try {
      await markDocumentReviewed(docId);
      setDocs((current) => current.filter((doc) => doc.id !== docId));
      setCount((current) => Math.max(0, current - 1));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingId(null);
    }
  }

  if (loading) {
    return (
      <section className="inbox">
        <div className="inbox-head">
          <p className="eyebrow">Review-Queue</p>
          <h2>Offene Dokumente werden geladen</h2>
        </div>
        <div className="inbox-list">
          {Array.from({ length: 4 }).map((_, i) => (
            <div className="inbox-row inbox-row--skeleton" key={i} />
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className="inbox">
      <div className="inbox-head">
        <div>
          <p className="eyebrow">Review-Queue</p>
          <h2>{count} offene {count === 1 ? "Prüfung" : "Prüfungen"}</h2>
        </div>
        <button type="button" className="link" onClick={load}>
          Aktualisieren
        </button>
      </div>

      {error && (
        <div className="state state--error">
          <strong>Inbox konnte nicht aktualisiert werden.</strong>
          <span>{error}</span>
        </div>
      )}

      {docs.length === 0 ? (
        <div className="state">
          <strong>Alles geprüft.</strong>
          <span>Neue fertige Dokumente erscheinen hier automatisch.</span>
        </div>
      ) : (
        <div className="inbox-list">
          {docs.map((doc) => (
            <article className="inbox-row" key={doc.id}>
              <Thumb doc={doc} />
              <div className="inbox-row__main">
                <button
                  type="button"
                  className="inbox-row__title"
                  onClick={() => onOpenDocument(doc.id)}
                  title={doc.title}
                >
                  {doc.title}
                </button>
                <p className="inbox-row__meta">
                  {doc.correspondent_name ?? "Unbekannter Korrespondent"}
                  {doc.document_type_name ? ` · ${doc.document_type_name}` : ""}
                </p>
                {doc.tags.length > 0 && (
                  <div className="inbox-row__tags">
                    {doc.tags.slice(0, 4).map((tag) => (
                      <span
                        key={tag.id}
                        className="tag"
                        style={{ borderColor: tag.color, color: tag.color }}
                      >
                        {tag.name}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <div className="inbox-row__side">
                <ProcessingBadge state={doc.processing_state} />
                <span className="muted">
                  {new Date(doc.added_at).toLocaleDateString("de-DE")}
                </span>
                <div className="inbox-row__actions">
                  <button type="button" onClick={() => onOpenDocument(doc.id)}>
                    Öffnen
                  </button>
                  {canEdit && (
                    <button
                      type="button"
                      onClick={() => markReviewed(doc.id)}
                      disabled={savingId === doc.id}
                    >
                      {savingId === doc.id ? "Speichere …" : "Als geprüft markieren"}
                    </button>
                  )}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
