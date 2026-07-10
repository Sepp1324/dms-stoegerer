import { useEffect, useMemo, useState } from "react";
import {
  downloadTimelineIcs,
  getTimeline,
  type TimelineBucket,
  type TimelineItem,
  type TimelineResult,
  type TimelineSource,
} from "../api";

const HORIZON_CHOICES = [7, 14, 30, 90] as const;
const BUCKETS: { key: TimelineBucket; label: string }[] = [
  { key: "overdue", label: "Überfällig" },
  { key: "today", label: "Heute" },
  { key: "soon", label: "Nächste 7 Tage" },
  { key: "upcoming", label: "Später" },
];

export default function DuePage({
  onOpenDocument,
}: {
  onOpenDocument: (documentId: number) => void;
}) {
  const [days, setDays] = useState<number>(30);
  const [timeline, setTimeline] = useState<TimelineResult | null>(null);
  const [sourceFilter, setSourceFilter] = useState<TimelineSource | "">("");
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    getTimeline(days)
      .then((result) => active && setTimeline(result))
      .catch((err) => active && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [days]);

  const filteredBuckets = useMemo(() => {
    if (!timeline) return null;
    if (!sourceFilter) return timeline.buckets;
    return Object.fromEntries(
      BUCKETS.map(({ key }) => [
        key,
        timeline.buckets[key].filter((item) => item.source === sourceFilter),
      ]),
    ) as TimelineResult["buckets"];
  }, [sourceFilter, timeline]);

  async function exportIcs() {
    setExporting(true);
    setError(null);
    try {
      const blob = await downloadTimelineIcs(days);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "dms-fristen.ics";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="due-view">
      <section className="due-hero">
        <div>
          <span className="due-eyebrow">Fristen-Center</span>
          <h2>Heute, bald, kritisch.</h2>
          <p>
            Wiedervorlagen, Vertragsfristen, Review-Aufgaben, Freigaben und
            Aufbewahrung in einer operativen Zeitachse.
          </p>
        </div>
        <div className="due-actions">
          <label className="filter">
            <span>Horizont</span>
            <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
              {HORIZON_CHOICES.map((choice) => (
                <option key={choice} value={choice}>
                  {choice} Tage
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={exportIcs} disabled={exporting || loading}>
            {exporting ? "Exportiere …" : "Kalender (.ics)"}
          </button>
        </div>
      </section>

      {timeline && (
        <>
          <section className="due-metrics" aria-label="Fristen Kennzahlen">
            <Metric label="Gesamt" value={timeline.summary.total} />
            <Metric label="Überfällig" value={timeline.summary.overdue} tone="danger" />
            <Metric label="Heute" value={timeline.summary.today} tone="warn" />
            <Metric label="Kritisch" value={timeline.summary.high} tone="danger" />
            <Metric label="Bald" value={timeline.summary.soon} />
          </section>

          <section className="due-source-tabs" aria-label="Fristen Quellen">
            <SourceButton
              active={sourceFilter === ""}
              label="Alle"
              count={timeline.summary.total}
              onClick={() => setSourceFilter("")}
            />
            {sourceOptions(timeline).map((source) => (
              <SourceButton
                key={source}
                active={sourceFilter === source}
                label={sourceLabel(source)}
                count={timeline.summary.by_source[source] ?? 0}
                onClick={() => setSourceFilter(source)}
              />
            ))}
          </section>
        </>
      )}

      {loading && <p className="muted">Fristen werden geladen …</p>}
      {error && <p className="status status--error">{error}</p>}

      {!loading && !error && timeline && filteredBuckets && (
        <div className="due-board">
          {BUCKETS.map(({ key, label }) => (
            <section className="due-group" key={key}>
              <h3 className="due-group__title">
                {label} <span className="muted">({filteredBuckets[key].length})</span>
              </h3>
              {filteredBuckets[key].length === 0 ? (
                <p className="muted">Keine Einträge.</p>
              ) : (
                <div className="due-list">
                  {filteredBuckets[key].map((item) => (
                    <TimelineCard
                      key={item.id}
                      item={item}
                      onOpenDocument={() => onOpenDocument(item.document)}
                    />
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "danger" | "warn";
}) {
  return (
    <div className={`due-metric${tone ? ` due-metric--${tone}` : ""}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function SourceButton({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`due-source${active ? " due-source--active" : ""}`}
      onClick={onClick}
    >
      {label}
      <span>{count}</span>
    </button>
  );
}

function TimelineCard({
  item,
  onOpenDocument,
}: {
  item: TimelineItem;
  onOpenDocument: () => void;
}) {
  return (
    <button
      type="button"
      className={`due-card due-card--${item.severity}`}
      onClick={onOpenDocument}
    >
      <span className={`due-card__date due-card__date--${item.bucket}`}>
        {relativeDate(item)}
      </span>
      <span className="due-card__body">
        <span className="due-card__head">
          <strong>{item.title}</strong>
          <span className={`due-chip due-chip--${item.source}`}>{sourceLabel(item.source)}</span>
        </span>
        <span className="due-card__title">{item.document_title}</span>
        <span className="due-card__desc">{item.description}</span>
      </span>
      <span className="due-card__action">{item.action_label}</span>
    </button>
  );
}

function sourceOptions(timeline: TimelineResult): TimelineSource[] {
  return (Object.keys(timeline.summary.by_source) as TimelineSource[]).sort((a, b) =>
    sourceLabel(a).localeCompare(sourceLabel(b), "de"),
  );
}

function sourceLabel(source: TimelineSource): string {
  if (source === "reminder") return "Wiedervorlage";
  if (source === "contract") return "Vertrag";
  if (source === "review_task") return "Review";
  if (source === "approval") return "Freigabe";
  return "Aufbewahrung";
}

function relativeDate(item: TimelineItem): string {
  if (item.days_delta < 0) return `seit ${Math.abs(item.days_delta)} T.`;
  if (item.days_delta === 0) return "heute";
  if (item.days_delta === 1) return "morgen";
  return `in ${item.days_delta} T.`;
}
