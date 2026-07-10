import { useEffect, useState } from "react";
import {
  getDocumentQuality,
  getQualityStatus,
  type BackupHealthStatus,
  type DocumentQuality,
  type DocumentQualityGrade,
  type EvidenceCheckStatus,
  type QualityIssue,
  type QualityStatus,
} from "../api";

function statusText(status: EvidenceCheckStatus | BackupHealthStatus): string {
  if (status === "ok") return "OK";
  if (status === "warn") return "Warnung";
  return "Fehler";
}

function statusTone(status: EvidenceCheckStatus | BackupHealthStatus): BackupHealthStatus {
  return status === "error" ? "error" : status === "warn" ? "warn" : "ok";
}

function gradeText(grade: DocumentQualityGrade): string {
  if (grade === "excellent") return "Exzellent";
  if (grade === "good") return "Gut";
  if (grade === "warning") return "Prüfen";
  return "Kritisch";
}

function categoryText(category: string): string {
  const labels: Record<string, string> = {
    archive: "Archiv",
    metadata: "Metadaten",
    ocr: "OCR",
    processing: "Verarbeitung",
    review: "Review",
  };
  return labels[category] || category;
}

function missingText(missing: string[]): string {
  if (missing.length === 0) return "Vollständig";
  const labels: Record<string, string> = {
    correspondent: "Korrespondent",
    created_at: "Belegdatum",
    document_type: "Dokumenttyp",
    folder: "Ordner",
    storage_path: "Ablagepfad",
    tags: "Tags",
    title: "Titel",
  };
  return missing.map((field) => labels[field] || field).join(", ");
}

function formatDate(value: string | null): string {
  if (!value) return "Nie";
  return new Intl.DateTimeFormat("de-AT", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
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
    <div className={`quality-metric quality-metric--${tone}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function QualityIssueRow({ issue }: { issue: QualityIssue }) {
  return (
    <div className={`quality-issue-row quality-issue-row--${issue.severity}`}>
      <div>
        <span className={`system-pill system-pill--${statusTone(issue.severity)}`}>
          {categoryText(issue.category)}
        </span>
        <strong>{issue.message}</strong>
      </div>
      <p>{issue.action}</p>
    </div>
  );
}

function QualityListItem({
  item,
  active,
  onSelect,
}: {
  item: DocumentQuality;
  active: boolean;
  onSelect: () => void;
}) {
  const mainIssue = item.issues[0];
  return (
    <button
      type="button"
      className={`quality-list-item quality-list-item--${item.status}${active ? " quality-list-item--active" : ""}`}
      onClick={onSelect}
    >
      <span className={`system-pill system-pill--${statusTone(item.status)}`}>
        {gradeText(item.grade)}
      </span>
      <strong>{item.title}</strong>
      <span className="quality-list-item__meta">
        {item.asn_label || "ohne ASN"} · Score {item.score}
      </span>
      {mainIssue && <span className="quality-list-item__reason">{mainIssue.message}</span>}
      <span className="quality-list-item__meta">
        OCR {item.summary.ocr.status_label} · Metadaten {item.summary.metadata.percent}%
      </span>
    </button>
  );
}

function QualityDetail({
  detail,
  busy,
  onOpenDocument,
}: {
  detail: DocumentQuality | null;
  busy: boolean;
  onOpenDocument: (id: number) => void;
}) {
  if (busy) {
    return <section className="quality-detail quality-detail--empty">Qualitätsprofil wird geladen …</section>;
  }
  if (!detail) {
    return (
      <section className="quality-detail quality-detail--empty">
        Kein Qualitätsproblem in den sichtbaren Dokumenten.
      </section>
    );
  }

  return (
    <section className={`quality-detail quality-detail--${detail.status}`}>
      <div className="quality-detail__head">
        <div>
          <span className={`system-pill system-pill--${statusTone(detail.status)}`}>
            {gradeText(detail.grade)}
          </span>
          <h3>{detail.title}</h3>
          <p>
            {detail.asn_label || "ohne ASN"} · Score {detail.score} · Archiv{" "}
            {detail.archive_status_label}
          </p>
        </div>
        <button type="button" onClick={() => onOpenDocument(detail.document_id)}>
          Öffnen
        </button>
      </div>

      <div className="quality-scorebar" aria-label={`Qualität ${detail.score} von 100`}>
        <span style={{ width: `${detail.score}%` }} />
      </div>

      <div className="quality-detail-grid">
        <div>
          <span>OCR</span>
          <strong>{detail.summary.ocr.status_label}</strong>
          <small>
            {detail.summary.ocr.text_length} Zeichen ·{" "}
            {detail.summary.ocr.page_count ?? "-"} Seiten
          </small>
        </div>
        <div>
          <span>Metadaten</span>
          <strong>{detail.summary.metadata.percent}%</strong>
          <small>{missingText(detail.summary.metadata.missing)}</small>
        </div>
        <div>
          <span>Archiv</span>
          <strong>{detail.summary.archive.status_label}</strong>
          <small>geprüft {formatDate(detail.summary.archive.checked_at)}</small>
        </div>
        <div>
          <span>Review</span>
          <strong>{detail.summary.review.status_label}</strong>
          <small>{detail.summary.review.open_tasks} offene Aufgaben</small>
        </div>
      </div>

      <div className="quality-check-grid">
        <div className={detail.summary.archive.archive_file ? "quality-check--ok" : "quality-check--warn"}>
          Archivdatei
        </div>
        <div className={detail.summary.archive.thumbnail ? "quality-check--ok" : "quality-check--warn"}>
          Vorschau
        </div>
        <div className={detail.summary.archive.immutable ? "quality-check--ok" : "quality-check--warn"}>
          WORM
        </div>
        <div className={detail.summary.archive.sealed ? "quality-check--ok" : "quality-check--warn"}>
          Siegel
        </div>
        <div className={detail.summary.archive.metadata_snapshot ? "quality-check--ok" : "quality-check--warn"}>
          Snapshot
        </div>
      </div>

      <div className="quality-block">
        <h4>Mängel und nächste Aktionen</h4>
        {detail.issues.length === 0 ? (
          <p className="muted">Keine offenen Qualitätsmängel.</p>
        ) : (
          <div className="quality-issue-list">
            {detail.issues.map((issue) => (
              <QualityIssueRow key={issue.code} issue={issue} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

export default function QualityCenterPage({
  onOpenDocument,
}: {
  onOpenDocument: (id: number) => void;
}) {
  const [status, setStatus] = useState<QualityStatus | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<DocumentQuality | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getQualityStatus()
      .then((data) => {
        if (cancelled) return;
        setStatus(data);
        setSelectedId(data.issues[0]?.document_id ?? null);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    getDocumentQuality(selectedId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  if (loading) {
    return <div className="quality-center">Lade Qualitätscenter …</div>;
  }
  if (error) {
    return <div className="quality-center error">{error}</div>;
  }
  if (!status) {
    return <div className="quality-center muted">Keine Qualitätsdaten verfügbar.</div>;
  }

  const summary = status.summary;
  return (
    <div className="quality-center">
      <section className={`quality-banner quality-banner--${status.status}`}>
        <div>
          <h2>Dokumentqualität</h2>
          <p>
            {summary.documents} Dokumente · Durchschnitt {summary.average_score}/100 ·{" "}
            aktualisiert {formatDate(status.generated_at)}
          </p>
        </div>
        <span className={`system-pill system-pill--${statusTone(status.status)}`}>
          {statusText(status.status)}
        </span>
      </section>

      <div className="quality-metrics">
        <Metric label="Durchschnitt" value={`${summary.average_score}/100`} tone={status.status} />
        <Metric label="Exzellent" value={summary.excellent} />
        <Metric label="Gut" value={summary.good} />
        <Metric label="Prüfen" value={summary.warning} tone="warn" />
        <Metric label="Kritisch" value={summary.critical} tone="error" />
        <Metric label="OCR-Themen" value={summary.ocr_issues} tone={summary.ocr_issues ? "warn" : "ok"} />
        <Metric
          label="Metadaten"
          value={summary.metadata_issues}
          tone={summary.metadata_issues ? "warn" : "ok"}
        />
        <Metric
          label="Archiv"
          value={summary.archive_issues}
          tone={summary.archive_issues ? "error" : "ok"}
        />
      </div>

      <div className="quality-layout">
        <section className="quality-list">
          <div className="quality-section-head">
            <h3>Priorisierte Dokumente</h3>
            <p>{status.issues.length} Dokumente mit offenen Qualitätsmängeln</p>
          </div>
          <div className="quality-list__items">
            {status.issues.length === 0 ? (
              <p className="muted">Alles sauber. Keine offenen Qualitätsmängel gefunden.</p>
            ) : (
              status.issues.map((item) => (
                <QualityListItem
                  key={item.document_id}
                  item={item}
                  active={item.document_id === selectedId}
                  onSelect={() => setSelectedId(item.document_id)}
                />
              ))
            )}
          </div>
        </section>
        <QualityDetail
          detail={detail}
          busy={detailLoading}
          onOpenDocument={onOpenDocument}
        />
      </div>
    </div>
  );
}
