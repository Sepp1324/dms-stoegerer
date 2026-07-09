import { useEffect, useState } from "react";
import {
  getDocumentEntities,
  scanKnowledgeEntities,
  type DocumentEntityLink,
} from "../../api";

export function EntitiesPanel({
  documentId,
  canEdit,
}: {
  documentId: number;
  canEdit: boolean;
}) {
  const [links, setLinks] = useState<DocumentEntityLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    getDocumentEntities(documentId)
      .then(setLinks)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Entitäten konnten nicht geladen werden."),
      )
      .finally(() => setLoading(false));
  }

  useEffect(load, [documentId]);

  async function rescan() {
    setBusy(true);
    setError(null);
    try {
      await scanKnowledgeEntities([documentId]);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="detail-section">
      <div className="detail-section__head">
        <div>
          <h3>Entitäten</h3>
          <p>Personen, Organisationen und Identifier aus diesem Dokument.</p>
        </div>
        {canEdit && (
          <button type="button" onClick={rescan} disabled={busy}>
            Neu scannen
          </button>
        )}
      </div>
      {error && <p className="status status--error">{error}</p>}
      {loading ? (
        <p className="muted">Lade Entitäten …</p>
      ) : links.length === 0 ? (
        <p className="muted">Noch keine Entitäten erkannt.</p>
      ) : (
        <div className="entity-link-list">
          {links.map((link) => (
            <article className="entity-link" key={link.id}>
              <div>
                <span className={`entity-kind entity-kind--${link.entity_kind}`}>
                  {link.entity_kind_label}
                </span>
                <strong>{link.entity_name}</strong>
                <small>
                  {link.role_label} · {link.source_label} · {link.confidence}%
                </small>
              </div>
              {link.source_snippet && <p>{link.source_snippet}</p>}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
