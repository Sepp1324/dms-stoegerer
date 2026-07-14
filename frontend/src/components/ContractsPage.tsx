import { useEffect, useMemo, useState } from "react";
import {
  confirmContract,
  getContracts,
  getContractSummary,
  scanContracts,
  type ContractRecord,
  type ContractQuery,
  type ContractStatus,
  type ContractSummary,
  type ContractType,
} from "../api";
import CostOverviewPanel from "./CostOverviewPanel";

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(`${value}T00:00:00`).toLocaleDateString("de-AT");
}

function formatMoney(record: ContractRecord): string {
  if (!record.amount) return "—";
  const amount = Number(record.amount);
  if (!Number.isFinite(amount)) return `${record.amount} ${record.currency}`;
  return new Intl.NumberFormat("de-AT", {
    style: "currency",
    currency: record.currency || "EUR",
  }).format(amount);
}

function daysUntil(value: string | null): number | null {
  if (!value) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(`${value}T00:00:00`);
  return Math.ceil((target.getTime() - today.getTime()) / 86_400_000);
}

function urgencyClass(value: string | null): string {
  const days = daysUntil(value);
  if (days === null) return "";
  if (days < 0) return " contract-chip--danger";
  if (days <= 30) return " contract-chip--danger";
  if (days <= 90) return " contract-chip--warn";
  return "";
}

function metricValue(summary: ContractSummary | null, key: keyof ContractSummary) {
  return summary ? summary[key] : "…";
}

export default function ContractsPage({
  canEdit,
  onOpenDocument,
}: {
  canEdit: boolean;
  onOpenDocument: (documentId: number) => void;
}) {
  const [summary, setSummary] = useState<ContractSummary | null>(null);
  const [contracts, setContracts] = useState<ContractRecord[]>([]);
  const [statusFilter, setStatusFilter] = useState<ContractStatus | "">("");
  const [typeFilter, setTypeFilter] = useState<ContractType | "">("");
  const [reviewFilter, setReviewFilter] = useState<"" | "true" | "false">("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = useMemo<ContractQuery>(
    () => ({
      status: statusFilter,
      contract_type: typeFilter,
      needs_review:
        reviewFilter === "" ? "" : reviewFilter === "true" ? true : false,
    }),
    [reviewFilter, statusFilter, typeFilter],
  );

  function load() {
    setLoading(true);
    setError(null);
    Promise.all([getContractSummary(), getContracts(query)])
      .then(([nextSummary, nextContracts]) => {
        setSummary(nextSummary);
        setContracts(nextContracts);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Verträge konnten nicht geladen werden.");
      })
      .finally(() => setLoading(false));
  }

  useEffect(load, [query]);

  async function handleScan() {
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const result = await scanContracts();
      setMessage(
        `${result.scanned} Dokumente gescannt · ${result.created} neu · ${result.updated} aktualisiert · ${result.no_contract} ohne Vertrag.`,
      );
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm(record: ContractRecord) {
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      await confirmContract(record.id);
      setMessage(`Vertrag "${record.provider_display || record.document_title}" bestätigt.`);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bestätigung fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="contracts-page">
      <CostOverviewPanel />
      <div className="contracts-toolbar">
        <div className="contract-metrics">
          <Metric label="Aktiv" value={metricValue(summary, "active")} />
          <Metric label="Zu prüfen" value={metricValue(summary, "needs_review")} tone="warn" />
          <Metric label="Kündigung ≤ 90T" value={metricValue(summary, "cancel_soon")} tone="danger" />
          <Metric label="Fällig ≤ 30T" value={metricValue(summary, "due_soon")} />
          <Metric label="Gesamt" value={metricValue(summary, "total")} />
        </div>
        <div className="contracts-actions">
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as ContractStatus | "")}>
            <option value="">Alle Status</option>
            <option value="active">Aktiv</option>
            <option value="unclear">Unklar</option>
            <option value="canceled">Gekündigt</option>
            <option value="expired">Abgelaufen</option>
          </select>
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value as ContractType | "")}>
            <option value="">Alle Typen</option>
            <option value="insurance">Versicherung</option>
            <option value="energy">Energie</option>
            <option value="telecom">Telekom</option>
            <option value="rent">Miete</option>
            <option value="loan">Kredit</option>
            <option value="subscription">Abo</option>
            <option value="public">Behörde</option>
            <option value="other">Sonstiges</option>
          </select>
          <select value={reviewFilter} onChange={(e) => setReviewFilter(e.target.value as "" | "true" | "false")}>
            <option value="">Alle Prüfstände</option>
            <option value="true">Nur zu prüfen</option>
            <option value="false">Bestätigt</option>
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

      {loading ? (
        <div className="contract-grid">
          {Array.from({ length: 4 }).map((_, i) => (
            <div className="contract-card contract-card--skeleton" key={i} />
          ))}
        </div>
      ) : contracts.length === 0 ? (
        <div className="empty-state">
          <h2>Keine Verträge im aktuellen Filter</h2>
          <p>Starte einen Bestandsscan oder warte, bis neue Dokumente verarbeitet wurden.</p>
        </div>
      ) : (
        <div className="contract-grid">
          {contracts.map((record) => (
            <article className="contract-card" key={record.id}>
              <header className="contract-card__head">
                <div>
                  <span className="contract-card__type">{record.contract_type_label}</span>
                  <h2>{record.provider_display || record.document_title}</h2>
                  <p>{record.contract_number || "Keine Vertragsnummer erkannt"}</p>
                </div>
                <span className={`status-pill status-pill--${record.status}`}>
                  {record.status_label}
                </span>
              </header>

              <div className="contract-card__facts">
                <span>
                  <strong>{formatMoney(record)}</strong>
                  <small>{record.billing_cycle_label}</small>
                </span>
                <span className={`contract-chip${urgencyClass(record.cancel_until)}`}>
                  Kündigen bis {formatDate(record.cancel_until)}
                </span>
                <span className={`contract-chip${urgencyClass(record.next_due_on)}`}>
                  Fällig {formatDate(record.next_due_on)}
                </span>
              </div>

              <div className="contract-card__meta">
                <span>Konfidenz {record.confidence}%</span>
                {record.case_file_title && <span>Akte: {record.case_file_title}</span>}
                {record.needs_review && <span className="review-dot">Prüfung offen</span>}
              </div>

              <footer className="contract-card__actions">
                <button type="button" className="link" onClick={() => onOpenDocument(record.document)}>
                  Dokument öffnen
                </button>
                {canEdit && record.needs_review && (
                  <button type="button" onClick={() => handleConfirm(record)} disabled={busy}>
                    Bestätigen
                  </button>
                )}
              </footer>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function Metric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string | number;
  tone?: "neutral" | "warn" | "danger";
}) {
  return (
    <div className={`contract-metric contract-metric--${tone}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}
