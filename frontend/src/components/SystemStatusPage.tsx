import { useEffect, useState } from "react";
import HouseholdPanel from "./HouseholdPanel";
import {
  getBackupStatus,
  getArchiveHealth,
  getOCRHealth,
  getSemanticIndexHealth,
  retryFailedOCRProcessing,
  runArchiveBulkCheck,
  type ArchiveHealthIssue,
  type ArchiveHealthStatus,
  type BackupHealthStatus,
  type BackupMonitorEntry,
  type BackupStatus,
  type OCRHealthIssue,
  type OCRHealthStatus,
  type SemanticIndexHealth,
} from "../api";

function formatDate(value: string | null): string {
  if (!value) return "Nie";
  return new Intl.DateTimeFormat("de-AT", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function statusLabel(status: BackupMonitorEntry["status"]): string {
  switch (status) {
    case "success":
      return "Erfolgreich";
    case "running":
      return "Läuft";
    case "failed":
      return "Fehlgeschlagen";
    default:
      return "Unbekannt";
  }
}

function toneFor(entry: BackupMonitorEntry): "ok" | "warn" | "error" {
  if (entry.status === "failed") return "error";
  if (entry.stale || entry.status === "unknown" || entry.status === "running") return "warn";
  return "ok";
}

function ageLabel(hours: number | null): string {
  if (hours === null) return "Keine erfolgreiche Ausführung";
  if (hours < 1) return "vor weniger als 1 Stunde";
  if (hours < 48) return `vor ${Math.round(hours)} Stunden`;
  return `vor ${Math.round(hours / 24)} Tagen`;
}

function StatusCard({
  title,
  entry,
  extra,
}: {
  title: string;
  entry: BackupMonitorEntry;
  extra?: string;
}) {
  const tone = toneFor(entry);
  return (
    <article className={`system-card system-card--${tone}`}>
      <div className="system-card__head">
        <h3>{title}</h3>
        <span className={`system-pill system-pill--${tone}`}>{statusLabel(entry.status)}</span>
      </div>
      <dl className="system-card__grid">
        <div>
          <dt>Letzter Erfolg</dt>
          <dd>{formatDate(entry.last_success_at)}</dd>
        </div>
        <div>
          <dt>Alter</dt>
          <dd>{ageLabel(entry.age_hours)}</dd>
        </div>
        <div>
          <dt>Artefakt</dt>
          <dd>{entry.artifact_timestamp || "-"}</dd>
        </div>
        <div>
          <dt>Zuletzt aktualisiert</dt>
          <dd>{formatDate(entry.updated_at)}</dd>
        </div>
      </dl>
      {entry.message && <p className="system-card__message">{entry.message}</p>}
      {extra && <p className="system-card__message">{extra}</p>}
    </article>
  );
}

function HealthMetric({
  label,
  value,
  tone = "ok",
}: {
  label: string;
  value: string | number;
  tone?: BackupHealthStatus;
}) {
  return (
    <div className={`system-metric system-metric--${tone}`}>
      <span className="system-metric__value">{value}</span>
      <span className="system-metric__label">{label}</span>
    </div>
  );
}

function issueReason(issue: OCRHealthIssue): string {
  if (issue.processing_state === "failed") return "Verarbeitung fehlgeschlagen";
  if (issue.ocr_status === "failed") return "OCR fehlgeschlagen";
  if (issue.ocr_text_length === 0) return "Kein OCR-Text";
  return "Prüfen";
}

function archiveReason(issue: ArchiveHealthIssue): string {
  if (issue.legal_hold) return "Legal Hold";
  if (issue.archive_status === "error") return issue.archive_error || "Archivfehler";
  if (issue.archive_status === "warning") return issue.archive_error || "Archivwarnung";
  if (issue.retention.state === "expired") return "Retention abgelaufen";
  if (issue.retention.state === "due_soon") return "Retention läuft bald ab";
  return issue.archive_status_label;
}

function retentionLabel(issue: ArchiveHealthIssue): string {
  if (issue.retention.state === "legal_hold") return "Legal Hold";
  if (!issue.retention.retention_until) return "Keine Frist";
  if (issue.retention.days_remaining === null) return issue.retention.retention_until;
  if (issue.retention.days_remaining < 0) {
    return `seit ${Math.abs(issue.retention.days_remaining)} Tagen abgelaufen`;
  }
  return `noch ${issue.retention.days_remaining} Tage`;
}

function ArchiveIssueList({ issues }: { issues: ArchiveHealthIssue[] }) {
  return (
    <section className="system-panel">
      <div className="system-panel__head">
        <div>
          <h3>Archiv-Auffälligkeiten</h3>
          <p>{issues.length ? `${issues.length} Dokumente mit Archivhinweis` : "Keine Archiv-Auffälligkeiten"}</p>
        </div>
      </div>
      {issues.length > 0 && (
        <div className="system-table-wrap">
          <table className="system-table">
            <thead>
              <tr>
                <th>Dokument</th>
                <th>Grund</th>
                <th>Archiv</th>
                <th>Retention</th>
              </tr>
            </thead>
            <tbody>
              {issues.map((issue) => (
                <tr key={issue.document_id}>
                  <td>
                    <span>{issue.title}</span>
                    <span className="system-table__sub">
                      ASN{String(issue.asn).padStart(6, "0")}
                    </span>
                  </td>
                  <td>{archiveReason(issue)}</td>
                  <td>{issue.archive_status_label}</td>
                  <td>{retentionLabel(issue)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function OCRIssueList({
  issues,
  retrying,
  onRetryAll,
}: {
  issues: OCRHealthIssue[];
  retrying: boolean;
  onRetryAll: () => void;
}) {
  const retryable = issues.filter((i) => i.can_retry).length;
  return (
    <section className="system-panel">
      <div className="system-panel__head">
        <div>
          <h3>OCR-/Verarbeitungsfehler</h3>
          <p>{issues.length ? `${issues.length} auffällige Dokumente` : "Keine offenen OCR-Auffälligkeiten"}</p>
        </div>
        {retryable > 0 && (
          <button onClick={onRetryAll} disabled={retrying}>
            {retrying ? "Starte …" : `${retryable} neu verarbeiten`}
          </button>
        )}
      </div>
      {issues.length > 0 && (
        <div className="system-table-wrap">
          <table className="system-table">
            <thead>
              <tr>
                <th>Dokument</th>
                <th>Grund</th>
                <th>Processing</th>
                <th>OCR</th>
                <th>Versuche</th>
              </tr>
            </thead>
            <tbody>
              {issues.map((issue) => (
                <tr key={issue.version_id}>
                  <td>
                    <span>{issue.document_title}</span>
                    <span className="system-table__sub">v{issue.version_no}</span>
                  </td>
                  <td>{issueReason(issue)}</td>
                  <td>{issue.processing_state}</td>
                  <td>{issue.ocr_status}</td>
                  <td>{issue.processing_attempts}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function SystemStatusPage() {
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [archive, setArchive] = useState<ArchiveHealthStatus | null>(null);
  const [ocr, setOcr] = useState<OCRHealthStatus | null>(null);
  const [semantic, setSemantic] = useState<SemanticIndexHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [retryNote, setRetryNote] = useState<string | null>(null);
  const [checkingArchive, setCheckingArchive] = useState(false);
  const [archiveNote, setArchiveNote] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    setRetryNote(null);
    setArchiveNote(null);
    Promise.all([
      getBackupStatus(),
      getArchiveHealth(),
      getOCRHealth(),
      getSemanticIndexHealth(),
    ])
      .then(([backupStatus, archiveStatus, ocrStatus, semanticStatus]) => {
        setStatus(backupStatus);
        setArchive(archiveStatus);
        setOcr(ocrStatus);
        setSemantic(semanticStatus);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  if (loading) return <p className="muted" role="status">Lade Systemstatus …</p>;
  if (error) {
    return (
      <section className="system-status">
        <p className="status status--error">{error}</p>
        <button onClick={load}>Erneut laden</button>
      </section>
    );
  }
  if (!status) return null;

  const tone: BackupHealthStatus =
    status.status === "error" || ocr?.status === "error" || archive?.status === "error"
      ? "error"
      : status.status === "warn"
          || ocr?.status === "warn"
          || archive?.status === "warn"
          || !!semantic?.missing_documents
        ? "warn"
        : "ok";
  const headline =
    tone === "ok"
      ? "System läuft"
      : tone === "warn"
        ? "System braucht Aufmerksamkeit"
        : "System hat einen Fehler";

  async function retryAllFailed() {
    setRetrying(true);
    setRetryNote(null);
    try {
      const result = await retryFailedOCRProcessing(25);
      setRetryNote(`${result.queued} Verarbeitung${result.queued === 1 ? "" : "en"} neu angestoßen.`);
      await Promise.all([
        getBackupStatus().then(setStatus),
        getArchiveHealth().then(setArchive),
        getOCRHealth().then(setOcr),
        getSemanticIndexHealth().then(setSemantic),
      ]);
    } catch (err) {
      setRetryNote(err instanceof Error ? err.message : String(err));
    } finally {
      setRetrying(false);
    }
  }

  async function checkArchive() {
    setCheckingArchive(true);
    setArchiveNote(null);
    try {
      const result = await runArchiveBulkCheck(50);
      setArchive(result.health);
      setArchiveNote(`${result.checked} Archivprüfung${result.checked === 1 ? "" : "en"} ausgeführt.`);
    } catch (err) {
      setArchiveNote(err instanceof Error ? err.message : String(err));
    } finally {
      setCheckingArchive(false);
    }
  }

  return (
    <section className="system-status">
      <div className={`system-banner system-banner--${tone}`}>
        <div>
          <h2>{headline}</h2>
          <p>
            Letztes Backup wird nach {status.cronjob.alert_after_hours} Stunden ohne
            erfolgreichen Lauf als veraltet markiert.
          </p>
        </div>
        <button onClick={load}>Aktualisieren</button>
      </div>

      <HouseholdPanel />

      <div className="system-grid">
        <StatusCard
          title="Backup-CronJob"
          entry={status.backup}
          extra={`CronJob ${status.cronjob.name}: ${status.cronjob.schedule}, erwartet alle ${status.cronjob.expected_interval_hours}h.`}
        />
        <StatusCard title="Restore-Drill" entry={status.restore_drill} />
      </div>

      {ocr && (
        <>
          <section className={`system-card system-card--${ocr.status}`}>
            <div className="system-card__head">
              <h3>OCR-Qualität</h3>
              <span className={`system-pill system-pill--${ocr.status}`}>
                {ocr.status === "ok" ? "OK" : ocr.status === "warn" ? "Warnung" : "Fehler"}
              </span>
            </div>
            <div className="system-metrics">
              <HealthMetric
                label="OCR-Erfolgsquote"
                value={`${ocr.summary.ocr_success_rate}%`}
                tone={ocr.summary.ocr_success_rate < ocr.thresholds.ocr_success_rate ? "warn" : "ok"}
              />
              <HealthMetric
                label="Ohne OCR-Text"
                value={ocr.summary.empty_ocr_text}
                tone={ocr.summary.empty_ocr_text ? "warn" : "ok"}
              />
              <HealthMetric
                label="OCR fehlgeschlagen"
                value={ocr.summary.ocr_failed}
                tone={ocr.summary.ocr_failed ? "warn" : "ok"}
              />
              <HealthMetric
                label="Processing failed"
                value={ocr.summary.processing_failed}
                tone={ocr.summary.processing_failed ? "error" : "ok"}
              />
              <HealthMetric
                label="Hängend"
                value={ocr.summary.stuck_processing}
                tone={ocr.summary.stuck_processing ? "warn" : "ok"}
              />
            </div>
            {ocr.oldest_stuck && (
              <p className="system-card__message">
                Älteste hängende Verarbeitung: {ocr.oldest_stuck.document_title}
              </p>
            )}
          </section>

          {retryNote && <p className="status status--warn">{retryNote}</p>}
          <OCRIssueList
            issues={ocr.issues}
            retrying={retrying}
            onRetryAll={retryAllFailed}
          />
        </>
      )}

      {archive && (
        <>
          <section className={`system-card system-card--${archive.status}`}>
            <div className="system-card__head">
              <h3>Archiv & Retention</h3>
              <span className={`system-pill system-pill--${archive.status}`}>
                {archive.status === "ok" ? "OK" : archive.status === "warn" ? "Warnung" : "Fehler"}
              </span>
            </div>
            <div className="system-metrics">
              <HealthMetric label="Geprüft OK" value={archive.summary.archive_ok} />
              <HealthMetric
                label="Warnungen"
                value={archive.summary.archive_warning}
                tone={archive.summary.archive_warning ? "warn" : "ok"}
              />
              <HealthMetric
                label="Fehler"
                value={archive.summary.archive_error}
                tone={archive.summary.archive_error ? "error" : "ok"}
              />
              <HealthMetric
                label="Ungeprüft"
                value={archive.summary.archive_unchecked}
                tone={archive.summary.archive_unchecked ? "warn" : "ok"}
              />
              <HealthMetric
                label="Legal Hold"
                value={archive.summary.legal_hold}
                tone={archive.summary.legal_hold ? "warn" : "ok"}
              />
              <HealthMetric
                label="Retention bald fällig"
                value={archive.summary.retention_due_soon}
                tone={archive.summary.retention_due_soon ? "warn" : "ok"}
              />
            </div>
            <p className="system-card__message">
              Dokumente werden {archive.thresholds.retention_due_soon_days} Tage vor Ablauf als fällig markiert.
            </p>
            <button onClick={checkArchive} disabled={checkingArchive}>
              {checkingArchive ? "Prüfe …" : "Archivprüfung starten"}
            </button>
          </section>
          {archiveNote && <p className="status status--warn">{archiveNote}</p>}
          <ArchiveIssueList issues={archive.issues} />
        </>
      )}

      {semantic && (
        <section className={`system-card system-card--${semantic.missing_documents ? "warn" : "ok"}`}>
          <div className="system-card__head">
            <h3>Semantischer Index</h3>
            <span className={`system-pill system-pill--${semantic.missing_documents ? "warn" : "ok"}`}>
              {semantic.missing_documents ? "Backfill nötig" : "OK"}
            </span>
          </div>
          <div className="system-metrics">
            <HealthMetric label="Indexiert" value={semantic.indexed_documents} />
            <HealthMetric
              label="Fehlend"
              value={semantic.missing_documents}
              tone={semantic.missing_documents ? "warn" : "ok"}
            />
            <HealthMetric label="Chunks" value={semantic.chunks} />
            <HealthMetric label="Dimensionen" value={semantic.dimension} />
          </div>
          <p className="system-card__message">
            Modell {semantic.model}. Backfill: <code>python manage.py reindex_embeddings</code>
          </p>
        </section>
      )}
    </section>
  );
}
