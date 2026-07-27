import { useEffect, useMemo, useState } from "react";
import {
  getEntityDocuments,
  getEntityRelations,
  getKnowledgeEntities,
  getKnowledgeSummary,
  scanKnowledgeEntities,
  type DocumentItem,
  type EntityRelation,
  type KnowledgeEntity,
  type KnowledgeEntityKind,
  type KnowledgeEntityQuery,
  type KnowledgeSummary,
} from "../api";

const KIND_OPTIONS: { value: KnowledgeEntityKind | ""; label: string }[] = [
  { value: "", label: "Alle Typen" },
  { value: "person", label: "Personen" },
  { value: "company", label: "Firmen" },
  { value: "authority", label: "Behörden" },
  { value: "iban", label: "IBANs" },
  { value: "email", label: "E-Mails" },
  { value: "phone", label: "Telefon" },
  { value: "contract_number", label: "Vertragsnummern" },
  { value: "policy_number", label: "Polizzennummern" },
  { value: "customer_number", label: "Kundennummern" },
  { value: "tax_number", label: "Steuernummern" },
];

function summaryCount(summary: KnowledgeSummary | null, kind: KnowledgeEntityKind) {
  return summary?.by_kind?.[kind] ?? 0;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("de-AT");
}

export default function KnowledgeGraphPage({
  canEdit,
  onOpenDocument,
}: {
  canEdit: boolean;
  onOpenDocument: (documentId: number) => void;
}) {
  const [summary, setSummary] = useState<KnowledgeSummary | null>(null);
  const [entities, setEntities] = useState<KnowledgeEntity[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedDocs, setSelectedDocs] = useState<DocumentItem[]>([]);
  const [selectedRelations, setSelectedRelations] = useState<EntityRelation[]>([]);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<KnowledgeEntityKind | "">("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = useMemo<KnowledgeEntityQuery>(
    () => ({ q: q.trim(), kind }),
    [kind, q],
  );

  const selected = entities.find((entity) => entity.id === selectedId) ?? null;

  const [reloadKey, setReloadKey] = useState(0);

  // Auto-Load bei Suche/Filter: debounced (kein Request je Tastendruck) + Stale-
  // Guard (P2). ``cancelled`` verhindert, dass die verspätete Antwort einer bereits
  // verworfenen Anfrage die aktuelle Liste überschreibt; das clearTimeout debounced.
  useEffect(() => {
    let cancelled = false;
    const handle = setTimeout(() => {
      setLoading(true);
      setError(null);
      Promise.all([getKnowledgeSummary(), getKnowledgeEntities(query)])
        .then(([nextSummary, nextEntities]) => {
          if (cancelled) return;
          setSummary(nextSummary);
          setEntities(nextEntities);
          setSelectedId((current) => current ?? nextEntities[0]?.id ?? null);
        })
        .catch((err) => {
          if (cancelled) return;
          setError(
            err instanceof Error
              ? err.message
              : "Gedächtnis konnte nicht geladen werden.",
          );
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [query, reloadKey]);

  useEffect(() => {
    if (!selectedId) {
      setSelectedDocs([]);
      setSelectedRelations([]);
      return;
    }
    // Stale-Guard (P2): Beim schnellen Wechsel zwischen Entitäten darf die
    // verspätete Antwort einer FRÜHEREN Auswahl die Details der aktuellen nicht
    // überschreiben.
    let cancelled = false;
    setDetailLoading(true);
    Promise.all([getEntityDocuments(selectedId), getEntityRelations(selectedId)])
      .then(([docs, relations]) => {
        if (cancelled) return;
        setSelectedDocs(docs);
        setSelectedRelations(relations);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Details konnten nicht geladen werden.");
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  async function handleScan() {
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const result = await scanKnowledgeEntities();
      setMessage(
        `${result.scanned} Dokumente gescannt · ${result.entities} Entitäten · ${result.links} Links · ${result.relations} Beziehungen.`,
      );
      setReloadKey((k) => k + 1);  // Reload über den (guarded) Auto-Load-Effekt
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="knowledge-page">
      <div className="knowledge-toolbar">
        <div className="knowledge-metrics">
          <Metric label="Entitäten" value={summary?.total ?? "…"} />
          <Metric label="Dokumente" value={summary?.documents_linked ?? "…"} />
          <Metric label="Personen" value={summaryCount(summary, "person")} />
          <Metric label="Firmen" value={summaryCount(summary, "company")} />
          <Metric label="Identifier" value={identifierCount(summary)} />
        </div>
        <div className="knowledge-actions">
          <input
            className="search"
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder="Entität, IBAN, Firma, Dokument …"
          />
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as KnowledgeEntityKind | "")}
          >
            {KIND_OPTIONS.map((option) => (
              <option key={option.value || "all"} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {canEdit && (
            <button type="button" onClick={handleScan} disabled={busy}>
              Bestand scannen
            </button>
          )}
        </div>
      </div>

      {message && <p className="inline-message">{message}</p>}
      {error && <p className="inline-error">{error}</p>}

      <div className="knowledge-layout">
        <div className="knowledge-list">
          {loading ? (
            Array.from({ length: 7 }).map((_, index) => (
              <div className="knowledge-row knowledge-row--skeleton" key={index} />
            ))
          ) : entities.length === 0 ? (
            <div className="empty-state">
              <h2>Keine Entitäten im aktuellen Filter</h2>
              <p>Starte einen Bestandsscan oder ändere den Filter.</p>
            </div>
          ) : (
            entities.map((entity) => (
              <button
                type="button"
                className={`knowledge-row${entity.id === selectedId ? " knowledge-row--active" : ""}`}
                key={entity.id}
                onClick={() => setSelectedId(entity.id)}
              >
                <span className={`entity-kind entity-kind--${entity.kind}`}>
                  {entity.kind_label}
                </span>
                <strong>{entity.name}</strong>
                <small>
                  {entity.document_count} Dokumente · {entity.relation_count} Beziehungen
                </small>
              </button>
            ))
          )}
        </div>

        <article className="knowledge-detail">
          {selected ? (
            <>
              <header className="knowledge-detail__head">
                <div>
                  <span className={`entity-kind entity-kind--${selected.kind}`}>
                    {selected.kind_label}
                  </span>
                  <h2>{selected.name}</h2>
                  <p>
                    Quelle {selected.source_label} · Konfidenz {selected.confidence}% · zuletzt{" "}
                    {formatDate(selected.last_seen_at)}
                  </p>
                </div>
              </header>

              {selected.identifiers.length > 0 && (
                <section>
                  <h3>Identifier</h3>
                  <div className="knowledge-chips">
                    {selected.identifiers.map((identifier) => (
                      <span className="knowledge-chip" key={identifier.id}>
                        {identifier.kind_label}: {identifier.value}
                      </span>
                    ))}
                  </div>
                </section>
              )}

              <section>
                <h3>Dokumente</h3>
                {detailLoading ? (
                  <p className="muted">Lade Details …</p>
                ) : selectedDocs.length === 0 ? (
                  <p className="muted">Keine sichtbaren Dokumente.</p>
                ) : (
                  <div className="knowledge-docs">
                    {selectedDocs.map((doc) => (
                      <button
                        type="button"
                        className="knowledge-doc"
                        key={doc.id}
                        onClick={() => onOpenDocument(doc.id)}
                      >
                        <strong>{doc.title}</strong>
                        <span>
                          {doc.correspondent_name || "Ohne Korrespondent"} ·{" "}
                          {new Date(doc.added_at).toLocaleDateString("de-AT")}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </section>

              <section>
                <h3>Beziehungen</h3>
                {selectedRelations.length === 0 ? (
                  <p className="muted">Noch keine Beziehungen erkannt.</p>
                ) : (
                  <div className="knowledge-relations">
                    {selectedRelations.map((relation) => {
                      const other =
                        relation.from_entity === selected.id
                          ? relation.to_name
                          : relation.from_name;
                      return (
                        <div className="knowledge-relation" key={relation.id}>
                          <strong>{other}</strong>
                          <span>
                            {relation.relation_type_label}
                            {relation.document_title ? ` · ${relation.document_title}` : ""}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>
            </>
          ) : (
            <div className="empty-state">
              <h2>Keine Entität ausgewählt</h2>
              <p>Wähle links einen Eintrag, um Dokumente und Beziehungen zu sehen.</p>
            </div>
          )}
        </article>
      </div>
    </section>
  );
}

function identifierCount(summary: KnowledgeSummary | null): number | string {
  if (!summary) return "…";
  return (
    (summary.by_kind.iban ?? 0) +
    (summary.by_kind.email ?? 0) +
    (summary.by_kind.phone ?? 0) +
    (summary.by_kind.contract_number ?? 0) +
    (summary.by_kind.policy_number ?? 0) +
    (summary.by_kind.customer_number ?? 0) +
    (summary.by_kind.tax_number ?? 0)
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="knowledge-metric">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}
