import { useEffect, useMemo, useState } from "react";
import {
  getDocumentTimeline,
  type DocumentTimeline,
  type TimelineCategory,
  type TimelineItem,
  type TimelineSeverity,
} from "../../api";

const CATEGORY_ORDER: TimelineCategory[] = [
  "processing",
  "metadata",
  "workflow",
  "security",
  "archive",
  "export",
  "system",
];

function formatDate(value: string | null): string {
  if (!value) return "Unbekannt";
  return new Intl.DateTimeFormat("de-AT", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function severityLabel(severity: TimelineSeverity): string {
  if (severity === "success") return "OK";
  if (severity === "warning") return "Warnung";
  if (severity === "error") return "Fehler";
  return "Info";
}

function detailRows(detail: Record<string, unknown>): [string, string][] {
  return Object.entries(detail).map(([key, value]) => [
    key.replace(/_/g, " "),
    typeof value === "string" ? value : JSON.stringify(value),
  ]);
}

function TimelineEntry({ item }: { item: TimelineItem }) {
  const rows = detailRows(item.detail || {});
  return (
    <li className={`timeline-item timeline-item--${item.severity}`}>
      <div className="timeline-item__dot" aria-hidden="true" />
      <article className="timeline-card">
        <div className="timeline-card__head">
          <div>
            <span className={`timeline-badge timeline-badge--${item.category}`}>
              {item.category_label}
            </span>
            <h4>{item.title}</h4>
          </div>
          <span className={`timeline-severity timeline-severity--${item.severity}`}>
            {severityLabel(item.severity)}
          </span>
        </div>
        <div className="timeline-card__meta">
          <time dateTime={item.timestamp ?? undefined}>{formatDate(item.timestamp)}</time>
          <span>{item.actor_name}</span>
          <span>{item.object_type}</span>
        </div>
        {item.summary && <p className="timeline-card__summary">{item.summary}</p>}
        {rows.length > 0 && (
          <details className="timeline-detail">
            <summary>Details</summary>
            <dl>
              {rows.map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </article>
    </li>
  );
}

export function TimelinePanel({ documentId }: { documentId: number }) {
  const [timeline, setTimeline] = useState<DocumentTimeline | null>(null);
  const [active, setActive] = useState<TimelineCategory | "all">("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setError(null);
    getDocumentTimeline(documentId)
      .then((data) => {
        if (mounted) setTimeline(data);
      })
      .catch((err) => mounted && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => mounted && setLoading(false));
    return () => {
      mounted = false;
    };
  }, [documentId]);

  const categories = useMemo(() => {
    const seen = new Map(timeline?.categories.map((cat) => [cat.id, cat.label]) ?? []);
    return CATEGORY_ORDER.filter((id) => seen.has(id)).map((id) => ({
      id,
      label: seen.get(id) || id,
    }));
  }, [timeline]);

  const items = useMemo(() => {
    const all = timeline?.results ?? [];
    if (active === "all") return all;
    return all.filter((item) => item.category === active);
  }, [active, timeline]);

  return (
    <section className="timeline-panel">
      <div className="timeline-panel__head">
        <div>
          <h3>Dokument-Timeline</h3>
          <p>
            {timeline ? `${timeline.count} Ereignisse` : "Chronologie wird geladen"}
            {timeline?.truncated ? ` · auf ${timeline.limit} begrenzt` : ""}
          </p>
        </div>
        <button type="button" className="link" onClick={() => setActive("all")}>
          Alle anzeigen
        </button>
      </div>

      {error && <p className="status status--error">{error}</p>}
      {loading && <p className="muted">Lade Timeline …</p>}

      {categories.length > 0 && (
        <div className="timeline-filters" aria-label="Timeline-Kategorien">
          <button
            type="button"
            className={active === "all" ? "timeline-filter timeline-filter--active" : "timeline-filter"}
            onClick={() => setActive("all")}
          >
            Alle
          </button>
          {categories.map((category) => (
            <button
              type="button"
              key={category.id}
              className={
                active === category.id
                  ? "timeline-filter timeline-filter--active"
                  : "timeline-filter"
              }
              onClick={() => setActive(category.id)}
            >
              {category.label}
            </button>
          ))}
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <p className="muted">Keine Ereignisse für diesen Filter.</p>
      )}

      {items.length > 0 && (
        <ol className="timeline-list">
          {items.map((item) => (
            <TimelineEntry key={item.id} item={item} />
          ))}
        </ol>
      )}
    </section>
  );
}
