import { useEffect, useState } from "react";
import {
  getDocumentEvidence,
  getDocumentRevisionPackage,
  getEvidenceStatus,
  type BackupHealthStatus,
  type EvidenceCheckStatus,
  type EvidenceDocumentReport,
  type EvidenceIssue,
  type EvidenceStatus,
} from "../api";

function formatDate(value: string | null): string {
  if (!value) return "Nie";
  return new Intl.DateTimeFormat("de-AT", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function statusText(status: EvidenceCheckStatus): string {
  if (status === "ok") return "OK";
  if (status === "warn") return "Warnung";
  return "Fehler";
}

function statusTone(status: EvidenceCheckStatus | BackupHealthStatus): BackupHealthStatus {
  return status === "error" ? "error" : status === "warn" ? "warn" : "ok";
}

function retentionText(issue: Pick<EvidenceIssue, "retention" | "legal_hold">): string {
  if (issue.legal_hold || issue.retention.state === "legal_hold") return "Legal Hold";
  if (!issue.retention.retention_until) return "Keine Frist";
  if (issue.retention.days_remaining === null) return issue.retention.retention_until;
  if (issue.retention.days_remaining < 0) {
    return `seit ${Math.abs(issue.retention.days_remaining)} Tagen abgelaufen`;
  }
  return `noch ${issue.retention.days_remaining} Tage`;
}

function safeFilename(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[^\w.-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 80) || "dokument";
}

function Metric({
  label,
  value,
  tone = "ok",
}: {
  label: string;
  value: string | number;
  tone?: BackupHealthStatus;
}) {
  return (
    <div className={`evidence-metric evidence-metric--${tone}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function IssueCard({
  issue,
  active,
  onSelect,
}: {
  issue: EvidenceIssue;
  active: boolean;
  onSelect: () => void;
}) {
  const firstRisk = issue.risks[0]?.message || issue.archive_status_label;
  return (
    <button
      type="button"
      className={`evidence-issue evidence-issue--${issue.status}${active ? " evidence-issue--active" : ""}`}
      onClick={onSelect}
    >
      <span className={`system-pill system-pill--${statusTone(issue.status)}`}>
        {statusText(issue.status)}
      </span>
      <strong>{issue.title}</strong>
      <span className="evidence-issue__meta">
        {issue.asn_label || `ASN${String(issue.asn).padStart(6, "0")}`} · Score {issue.score}
      </span>
      <span className="evidence-issue__reason">{firstRisk}</span>
      <span className="evidence-issue__meta">
        Archiv: {issue.archive_status_label} · {retentionText(issue)}
      </span>
    </button>
  );
}

function EvidenceDetail({
  report,
  busy,
  onDownload,
  onOpenDocument,
}: {
  report: EvidenceDocumentReport | null;
  busy: boolean;
  onDownload: (report: EvidenceDocumentReport) => void;
  onOpenDocument: (id: number) => void;
}) {
  if (busy) {
    return <section className="evidence-detail evidence-detail--empty">Prüfe Dokumentnachweis …</section>;
  }
  if (!report) {
    return (
      <section className="evidence-detail evidence-detail--empty">
        Ein Dokument auswählen, um Hash-Kette, Siegel, Versionen und Audit-Spur frisch zu prüfen.
      </section>
    );
  }

  return (
    <section className={`evidence-detail evidence-detail--${report.status}`}>
      <div className="evidence-detail__head">
        <div>
          <span className={`system-pill system-pill--${statusTone(report.status)}`}>
            {statusText(report.status)}
          </span>
          <h3>{report.title}</h3>
          <p>
            {report.asn_label} · Score {report.score} · Archivprüfung {formatDate(report.archive_checked_at)}
          </p>
        </div>
        <div className="evidence-actions">
          <button type="button" onClick={() => onOpenDocument(report.document_id)}>
            Öffnen
          </button>
          <button type="button" onClick={() => onDownload(report)}>
            Revisionspaket
          </button>
        </div>
      </div>

      <div className="evidence-detail-grid">
        <div>
          <span>Korrespondent</span>
          <strong>{report.correspondent || "-"}</strong>
        </div>
        <div>
          <span>Typ</span>
          <strong>{report.document_type || "-"}</strong>
        </div>
        <div>
          <span>Ordner</span>
          <strong>{report.folder || report.storage_path || "-"}</strong>
        </div>
        <div>
          <span>Audit-Einträge</span>
          <strong>{report.audit.count}</strong>
        </div>
      </div>

      {report.risks.length > 0 && (
        <div className="evidence-block">
          <h4>Risiken</h4>
          <div className="evidence-risk-list">
            {report.risks.map((risk) => (
              <span key={risk.code} className={`evidence-risk evidence-risk--${risk.severity}`}>
                {risk.message}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="evidence-block">
        <h4>Prüfpunkte</h4>
        <div className="evidence-check-grid">
          {report.checks.map((check) => (
            <div key={check.code} className={`evidence-check evidence-check--${check.status}`}>
              <span>{check.code.replace(/_/g, " ")}</span>
              <strong>{statusText(check.status)}</strong>
              <small>{check.detail || "-"}</small>
            </div>
          ))}
        </div>
      </div>

      <div className="evidence-block">
        <h4>Versionen</h4>
        <div className="system-table-wrap">
          <table className="system-table">
            <thead>
              <tr>
                <th>Version</th>
                <th>Dateien</th>
                <th>Siegel</th>
                <th>State</th>
              </tr>
            </thead>
            <tbody>
              {report.versions.map((version) => (
                <tr key={version.id}>
                  <td>v{version.version_no}</td>
                  <td>
                    Original {version.file_present ? "OK" : "fehlt"} · Archiv{" "}
                    {version.archive_present ? "OK" : "fehlt"}
                  </td>
                  <td>{version.seal_ok ? "OK" : "fehlt/ungültig"}</td>
                  <td>{version.processing_state}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="evidence-block">
        <h4>Letzte Audit-Ereignisse</h4>
        {report.audit.latest.length === 0 ? (
          <p className="muted">Keine Audit-Ereignisse vorhanden.</p>
        ) : (
          <div className="evidence-audit-list">
            {report.audit.latest.map((entry) => (
              <div key={entry.id} className="evidence-audit-entry">
                <strong>{entry.action}</strong>
                <span>
                  {formatDate(entry.timestamp)} · {entry.actor || "System"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

export default function EvidenceCenterPage({
  onOpenDocument,
}: {
  onOpenDocument: (id: number) => void;
}) {
  const [status, setStatus] = useState<EvidenceStatus | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [report, setReport] = useState<EvidenceDocumentReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailBusy, setDetailBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    getEvidenceStatus()
      .then((data) => {
        setStatus(data);
        if (selectedId === null && data.issues.length > 0) {
          setSelectedId(data.issues[0].document_id);
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (selectedId === null) {
      setReport(null);
      return;
    }
    setDetailBusy(true);
    setNotice(null);
    getDocumentEvidence(selectedId)
      .then(setReport)
      .catch((err) => setNotice(err instanceof Error ? err.message : String(err)))
      .finally(() => setDetailBusy(false));
  }, [selectedId]);

  async function downloadRevisionPackage(reportToDownload: EvidenceDocumentReport) {
    setDownloading(true);
    setNotice(null);
    try {
      const blob = await getDocumentRevisionPackage(reportToDownload.document_id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${safeFilename(reportToDownload.title)}-revisionspaket.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setNotice("Revisionspaket wurde erstellt.");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  }

  if (loading) return <p className="muted" role="status">Lade Beweis-Center …</p>;
  if (error) {
    return (
      <section className="evidence-center">
        <p className="status status--error">{error}</p>
        <button type="button" onClick={load}>Erneut laden</button>
      </section>
    );
  }
  if (!status) return null;

  const headline =
    status.status === "ok"
      ? "Alle Nachweise belastbar"
      : status.status === "warn"
        ? "Nachweise brauchen Prüfung"
        : "Nachweise fehlerhaft";

  return (
    <section className="evidence-center">
      <div className={`evidence-banner evidence-banner--${status.status}`}>
        <div>
          <h2>{headline}</h2>
          <p>
            Stand {formatDate(status.generated_at)} · {status.summary.documents} sichtbare Dokumente
          </p>
        </div>
        <button type="button" onClick={load}>Aktualisieren</button>
      </div>

      <div className="evidence-metrics">
        <Metric label="Beweis OK" value={status.summary.evidence_ok} />
        <Metric label="Warnungen" value={status.summary.warnings} tone={status.summary.warnings ? "warn" : "ok"} />
        <Metric label="Fehler" value={status.summary.errors} tone={status.summary.errors ? "error" : "ok"} />
        <Metric label="Ungeprüft" value={status.summary.unchecked} tone={status.summary.unchecked ? "warn" : "ok"} />
        <Metric label="Archiv-PDF fehlt" value={status.summary.archive_missing} tone={status.summary.archive_missing ? "warn" : "ok"} />
        <Metric label="Hash-Kette defekt" value={status.summary.hash_chain_errors} tone={status.summary.hash_chain_errors ? "error" : "ok"} />
        <Metric label="Seal fehlt" value={status.summary.seal_missing} tone={status.summary.seal_missing ? "error" : "ok"} />
        <Metric label="Legal Hold" value={status.summary.legal_hold} tone={status.summary.legal_hold ? "warn" : "ok"} />
      </div>

      {notice && (
        <p className={`status ${downloading ? "status--warn" : "status--ok"}`}>
          {notice}
        </p>
      )}

      <div className="evidence-layout">
        <section className="evidence-issues">
          <div className="evidence-section-head">
            <div>
              <h3>Auffälligkeiten</h3>
              <p>{status.issues.length ? `${status.issues.length} Dokumente` : "Keine offenen Nachweise"}</p>
            </div>
          </div>
          {status.issues.length === 0 ? (
            <p className="muted">Keine Risiken gefunden.</p>
          ) : (
            <div className="evidence-issue-list">
              {status.issues.map((issue) => (
                <IssueCard
                  key={issue.document_id}
                  issue={issue}
                  active={selectedId === issue.document_id}
                  onSelect={() => setSelectedId(issue.document_id)}
                />
              ))}
            </div>
          )}
        </section>

        <EvidenceDetail
          report={report}
          busy={detailBusy}
          onDownload={downloadRevisionPackage}
          onOpenDocument={onOpenDocument}
        />
      </div>
    </section>
  );
}
