import { useEffect, useMemo, useState } from "react";
import {
  getArchiveHealth,
  getBackupStatus,
  getContractSummary,
  getDocuments,
  getInboxSummary,
  getOCRHealth,
  getQualityStatus,
  getTimeline,
  type ArchiveHealthStatus,
  type BackupHealthStatus,
  type BackupStatus,
  type ContractSummary,
  type DocumentItem,
  type InboxSummary,
  type OCRHealthStatus,
  type Paginated,
  type QualityStatus,
  type TimelineItem,
  type TimelineResult,
} from "../api";
import { ProcessingBadge } from "./ProcessingStatus";

type DashboardTarget =
  | "docs"
  | "inbox"
  | "quality"
  | "system"
  | "contracts"
  | "faellig"
  | "capture"
  | "evidence";

type LoadResult<T> = {
  data: T | null;
  error: string | null;
};

type Tone = "ok" | "warn" | "error" | "neutral";

interface DashboardData {
  inbox: LoadResult<InboxSummary>;
  quality: LoadResult<QualityStatus>;
  timeline: LoadResult<TimelineResult>;
  documents: LoadResult<Paginated<DocumentItem>>;
  contracts: LoadResult<ContractSummary>;
  backup: LoadResult<BackupStatus>;
  ocr: LoadResult<OCRHealthStatus>;
  archive: LoadResult<ArchiveHealthStatus>;
}

const empty = <T,>(): LoadResult<T> => ({ data: null, error: null });

async function capture<T>(promise: Promise<T>): Promise<LoadResult<T>> {
  try {
    return { data: await promise, error: null };
  } catch (error) {
    return {
      data: null,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

function formatDateTime(value: string | null): string {
  if (!value) return "Nie";
  return new Intl.DateTimeFormat("de-AT", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("de-AT", {
    dateStyle: "medium",
  }).format(new Date(value));
}

function percent(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return `${Math.round(value)}%`;
}

function worstTone(values: (BackupHealthStatus | null | undefined)[]): Tone {
  if (values.includes("error")) return "error";
  if (values.includes("warn")) return "warn";
  if (values.includes("ok")) return "ok";
  return "neutral";
}

function toneLabel(tone: Tone): string {
  if (tone === "ok") return "OK";
  if (tone === "warn") return "Prüfen";
  if (tone === "error") return "Kritisch";
  return "Unbekannt";
}

function staleLabel(hours: number | null): string {
  if (hours === null) return "kein Erfolg";
  if (hours < 1) return "< 1h";
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

function qualityTone(status: BackupHealthStatus | null | undefined): Tone {
  if (status === "error") return "error";
  if (status === "warn") return "warn";
  if (status === "ok") return "ok";
  return "neutral";
}

function timelineItems(timeline: TimelineResult | null): TimelineItem[] {
  if (!timeline) return [];
  return [...timeline.items].sort((a, b) => a.days_delta - b.days_delta).slice(0, 6);
}

function relativeDate(item: TimelineItem): string {
  if (item.days_delta < 0) return `seit ${Math.abs(item.days_delta)} T.`;
  if (item.days_delta === 0) return "heute";
  if (item.days_delta === 1) return "morgen";
  return `in ${item.days_delta} T.`;
}

function sourceLabel(source: TimelineItem["source"]): string {
  if (source === "reminder") return "Wiedervorlage";
  if (source === "contract") return "Vertrag";
  if (source === "review_task") return "Review";
  if (source === "approval") return "Freigabe";
  return "Aufbewahrung";
}

export default function DashboardPage({
  canWrite,
  isAdmin,
  onNavigate,
  onOpenDocument,
}: {
  canWrite: boolean;
  isAdmin: boolean;
  onNavigate: (target: DashboardTarget) => void;
  onOpenDocument: (documentId: number) => void;
}) {
  const [data, setData] = useState<DashboardData>({
    inbox: empty(),
    quality: empty(),
    timeline: empty(),
    documents: empty(),
    contracts: empty(),
    backup: empty(),
    ocr: empty(),
    archive: empty(),
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([
      capture(getInboxSummary()),
      capture(getQualityStatus()),
      capture(getTimeline(30)),
      capture(getDocuments({ ordering: "-added_at", page: 1 })),
      capture(getContractSummary()),
      isAdmin ? capture(getBackupStatus()) : Promise.resolve(empty<BackupStatus>()),
      isAdmin ? capture(getOCRHealth()) : Promise.resolve(empty<OCRHealthStatus>()),
      isAdmin ? capture(getArchiveHealth()) : Promise.resolve(empty<ArchiveHealthStatus>()),
    ])
      .then(([inbox, quality, timeline, documents, contracts, backup, ocr, archive]) => {
        if (!active) return;
        setData({ inbox, quality, timeline, documents, contracts, backup, ocr, archive });
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [isAdmin]);

  const recentDocuments = data.documents.data?.results.slice(0, 6) ?? [];
  const dueItems = useMemo(() => timelineItems(data.timeline.data), [data.timeline.data]);
  const qualityIssues = data.quality.data?.issues.slice(0, 5) ?? [];
  const openWork =
    (data.inbox.data?.open_review_tasks ?? 0) +
    (data.quality.data?.summary.critical ?? 0) +
    (data.contracts.data?.needs_review ?? 0) +
    (data.timeline.data?.summary.overdue ?? 0);
  const healthTone = worstTone([
    data.backup.data?.status,
    data.quality.data?.status,
    data.ocr.data?.status,
    data.archive.data?.status,
  ]);

  return (
    <section className="dashboard">
      <section className={`dashboard-hero dashboard-hero--${healthTone}`}>
        <div className="dashboard-hero__copy">
          <span className="dashboard-eyebrow">DMS-Cockpit</span>
          <h2>{loading ? "Lade Überblick …" : `${openWork} offene Signale`}</h2>
          <p>
            {data.documents.data?.count ?? "—"} Dokumente · Qualität{" "}
            {data.quality.data?.summary.average_score ?? "—"}/100 · System{" "}
            {toneLabel(healthTone)}
          </p>
        </div>
        <div className="dashboard-hero__actions">
          {canWrite && (
            <button type="button" onClick={() => onNavigate("capture")}>
              Erfassen
            </button>
          )}
          <button type="button" className="secondary" onClick={() => onNavigate("docs")}>
            Dokumente
          </button>
          <button type="button" className="secondary" onClick={() => onNavigate("inbox")}>
            Inbox
          </button>
        </div>
      </section>

      <section className="dashboard-metrics" aria-label="Cockpit Kennzahlen">
        <MetricCard
          label="Inbox"
          value={data.inbox.data?.total_needs_review ?? "—"}
          detail={`${data.inbox.data?.open_review_tasks ?? "—"} offene Aufgaben`}
          tone={(data.inbox.data?.open_review_tasks ?? 0) > 0 ? "warn" : "ok"}
          onClick={() => onNavigate("inbox")}
        />
        <MetricCard
          label="Qualität"
          value={data.quality.data?.summary.average_score ?? "—"}
          detail={`${data.quality.data?.summary.critical ?? "—"} kritisch`}
          tone={qualityTone(data.quality.data?.status)}
          onClick={() => onNavigate("quality")}
        />
        <MetricCard
          label="Fristen"
          value={data.timeline.data?.summary.overdue ?? "—"}
          detail={`${data.timeline.data?.summary.today ?? "—"} heute`}
          tone={(data.timeline.data?.summary.overdue ?? 0) > 0 ? "error" : "ok"}
          onClick={() => onNavigate("faellig")}
        />
        <MetricCard
          label="Verträge"
          value={data.contracts.data?.needs_review ?? "—"}
          detail={`${data.contracts.data?.cancel_soon ?? "—"} kündigen ≤ 90T`}
          tone={(data.contracts.data?.needs_review ?? 0) > 0 ? "warn" : "ok"}
          onClick={() => onNavigate("contracts")}
        />
      </section>

      <div className="dashboard-layout">
        <section className="dashboard-main">
          <PanelHeader
            title="Arbeitsliste"
            meta={data.quality.data ? `${data.quality.data.issues.length} Qualitätsmängel` : "—"}
            actionLabel="Qualität öffnen"
            onAction={() => onNavigate("quality")}
          />
          {qualityIssues.length === 0 ? (
            <div className="dashboard-empty">Keine kritischen Qualitätsmängel.</div>
          ) : (
            <div className="dashboard-worklist">
              {qualityIssues.map((issue) => (
                <button
                  type="button"
                  key={issue.document_id}
                  className={`dashboard-workitem dashboard-workitem--${issue.status}`}
                  onClick={() => onOpenDocument(issue.document_id)}
                >
                  <span className={`system-pill system-pill--${issue.status}`}>
                    {issue.grade === "critical" ? "Kritisch" : "Prüfen"}
                  </span>
                  <strong>{issue.title}</strong>
                  <span>{issue.issues[0]?.message ?? "Qualität prüfen"}</span>
                  <small>
                    {issue.asn_label || "ohne ASN"} · Score {issue.score}
                  </small>
                </button>
              ))}
            </div>
          )}

          <PanelHeader
            title="Neue Dokumente"
            meta={`${data.documents.data?.count ?? "—"} im Archiv`}
            actionLabel="Dokumente öffnen"
            onAction={() => onNavigate("docs")}
          />
          <div className="dashboard-doc-list">
            {recentDocuments.map((doc) => (
              <button
                type="button"
                key={doc.id}
                className="dashboard-doc"
                onClick={() => onOpenDocument(doc.id)}
              >
                <span className="dashboard-doc__body">
                  <strong>{doc.title}</strong>
                  <span>
                    {doc.correspondent_name || "Unbekannt"}
                    {doc.document_type_name ? ` · ${doc.document_type_name}` : ""}
                  </span>
                </span>
                <span className="dashboard-doc__meta">
                  <ProcessingBadge state={doc.processing_state} />
                  <small>{formatDate(doc.created_at || doc.added_at)}</small>
                </span>
              </button>
            ))}
            {!loading && recentDocuments.length === 0 && (
              <div className="dashboard-empty">Keine Dokumente vorhanden.</div>
            )}
          </div>
        </section>

        <aside className="dashboard-side">
          <section className="dashboard-panel">
            <PanelHeader
              title="System"
              meta={toneLabel(healthTone)}
              actionLabel={isAdmin ? "System öffnen" : undefined}
              onAction={isAdmin ? () => onNavigate("system") : undefined}
            />
            <div className="dashboard-health">
              <HealthLine
                label="Backup"
                value={data.backup.data ? staleLabel(data.backup.data.backup.age_hours) : "—"}
                tone={qualityTone(data.backup.data?.backup.stale ? "warn" : data.backup.data?.status)}
                detail={data.backup.error || `letzter Erfolg ${formatDateTime(data.backup.data?.backup.last_success_at ?? null)}`}
              />
              <HealthLine
                label="Restore"
                value={data.backup.data ? staleLabel(data.backup.data.restore_drill.age_hours) : "—"}
                tone={qualityTone(data.backup.data?.restore_drill.stale ? "warn" : data.backup.data?.status)}
                detail={formatDateTime(data.backup.data?.restore_drill.last_success_at ?? null)}
              />
              <HealthLine
                label="OCR"
                value={percent(data.ocr.data?.summary.ocr_success_rate)}
                tone={qualityTone(data.ocr.data?.status)}
                detail={`${data.ocr.data?.summary.ocr_failed ?? "—"} Fehler · ${data.ocr.data?.summary.empty_ocr_text ?? "—"} leer`}
              />
              <HealthLine
                label="Archiv"
                value={data.archive.data?.summary.archive_error ?? "—"}
                tone={qualityTone(data.archive.data?.status)}
                detail={`${data.archive.data?.summary.archive_unchecked ?? "—"} ungeprüft`}
              />
            </div>
          </section>

          <section className="dashboard-panel">
            <PanelHeader
              title="Fristen"
              meta={data.timeline.data ? `${data.timeline.data.summary.total} Einträge` : "—"}
              actionLabel="Fristen öffnen"
              onAction={() => onNavigate("faellig")}
            />
            <div className="dashboard-timeline">
              {dueItems.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={`dashboard-timeline-item dashboard-timeline-item--${item.severity}`}
                  onClick={() => onOpenDocument(item.document)}
                >
                  <span>{relativeDate(item)}</span>
                  <strong>{item.title}</strong>
                  <small>
                    {sourceLabel(item.source)} · {item.document_title}
                  </small>
                </button>
              ))}
              {!loading && dueItems.length === 0 && (
                <div className="dashboard-empty">Keine offenen Fristen.</div>
              )}
            </div>
          </section>

          <section className="dashboard-panel">
            <PanelHeader title="Schnellzugriff" meta="Arbeitsbereiche" />
            <div className="dashboard-shortcuts">
              <Shortcut label="Inbox" value={data.inbox.data?.total_needs_review ?? "—"} onClick={() => onNavigate("inbox")} />
              <Shortcut label="Beweise" value={data.archive.data?.summary.archive_error ?? "—"} onClick={() => onNavigate("evidence")} />
              <Shortcut label="Verträge" value={data.contracts.data?.active ?? "—"} onClick={() => onNavigate("contracts")} />
              <Shortcut label="Dokumente" value={data.documents.data?.count ?? "—"} onClick={() => onNavigate("docs")} />
            </div>
          </section>
        </aside>
      </div>
    </section>
  );
}

function MetricCard({
  label,
  value,
  detail,
  tone,
  onClick,
}: {
  label: string;
  value: string | number;
  detail: string;
  tone: Tone;
  onClick: () => void;
}) {
  return (
    <button type="button" className={`dashboard-metric dashboard-metric--${tone}`} onClick={onClick}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </button>
  );
}

function PanelHeader({
  title,
  meta,
  actionLabel,
  onAction,
}: {
  title: string;
  meta: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <header className="dashboard-panel-head">
      <div>
        <h3>{title}</h3>
        <span>{meta}</span>
      </div>
      {actionLabel && onAction && (
        <button type="button" className="link" onClick={onAction}>
          {actionLabel}
        </button>
      )}
    </header>
  );
}

function HealthLine({
  label,
  value,
  tone,
  detail,
}: {
  label: string;
  value: string | number;
  tone: Tone;
  detail: string;
}) {
  return (
    <div className={`dashboard-health-line dashboard-health-line--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function Shortcut({
  label,
  value,
  onClick,
}: {
  label: string;
  value: string | number;
  onClick: () => void;
}) {
  return (
    <button type="button" className="dashboard-shortcut" onClick={onClick}>
      <strong>{value}</strong>
      <span>{label}</span>
    </button>
  );
}
