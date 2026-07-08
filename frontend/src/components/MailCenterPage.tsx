import { useEffect, useMemo, useState } from "react";
import {
  getMailAccounts,
  getProcessedMails,
  getProcessedMailSummary,
  markProcessedMailIgnored,
  type MailAccount,
  type ProcessedMail,
  type ProcessedMailStatus,
  type ProcessedMailSummary,
} from "../api";
import MailAccountsAdmin from "./MailAccountsAdmin";

const STATUS_OPTIONS: { value: ProcessedMailStatus | ""; label: string }[] = [
  { value: "", label: "Alle Status" },
  { value: "imported", label: "Importiert" },
  { value: "partial", label: "Teilweise" },
  { value: "ignored", label: "Ignoriert" },
  { value: "failed", label: "Fehlerhaft" },
];

function statusTone(status: ProcessedMailStatus): string {
  if (status === "imported") return "ok";
  if (status === "partial") return "warn";
  if (status === "failed") return "error";
  return "muted";
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="mail-center-metric">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function MailRow({
  item,
  canEdit,
  onOpenDocument,
  onChanged,
}: {
  item: ProcessedMail;
  canEdit: boolean;
  onOpenDocument: (id: number) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ignore() {
    setBusy(true);
    setError(null);
    try {
      await markProcessedMailIgnored(item.id, "Im Mail-Center ignoriert.");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const dateValue = item.received_at || item.processed_at;

  return (
    <article className="mail-center-row">
      <div className="mail-center-row__main">
        <div className="mail-center-row__head">
          <span className={`mail-center-status mail-center-status--${statusTone(item.status)}`}>
            {item.status_label}
          </span>
          <span className="mail-center-row__subject" title={item.subject || "(ohne Betreff)"}>
            {item.subject || "(ohne Betreff)"}
          </span>
        </div>
        <p className="mail-center-row__meta">
          {item.sender || "Unbekannter Absender"} · {item.account_name} ·{" "}
          {new Date(dateValue).toLocaleString("de-DE")}
        </p>
        {item.attachment_names.length > 0 && (
          <div className="mail-center-attachments">
            {item.attachment_names.slice(0, 5).map((name) => (
              <span key={`${item.id}-${name}`}>{name}</span>
            ))}
            {item.attachment_names.length > 5 && (
              <span>+{item.attachment_names.length - 5}</span>
            )}
          </div>
        )}
        {item.imported_documents.length > 0 && (
          <div className="mail-center-docs">
            {item.imported_documents.map((doc) => (
              <button
                type="button"
                className="link"
                key={doc.id}
                onClick={() => onOpenDocument(doc.id)}
              >
                {doc.asn_label ? `${doc.asn_label} · ` : ""}
                {doc.title}
              </button>
            ))}
          </div>
        )}
        {item.note && <p className="mail-center-note">{item.note}</p>}
        {item.error && <p className="status status--error">{item.error}</p>}
        {error && <p className="status status--error">{error}</p>}
      </div>
      <div className="mail-center-row__side">
        <span className="muted">
          {item.imported_count}/{item.attachment_count} importiert
        </span>
        {canEdit && item.status !== "ignored" && (
          <button type="button" className="link" onClick={ignore} disabled={busy}>
            {busy ? "Speichere …" : "Ignorieren"}
          </button>
        )}
      </div>
    </article>
  );
}

export default function MailCenterPage({
  canEdit,
  onOpenDocument,
}: {
  canEdit: boolean;
  onOpenDocument: (id: number) => void;
}) {
  const [tab, setTab] = useState<"center" | "accounts">("center");
  const [items, setItems] = useState<ProcessedMail[]>([]);
  const [summary, setSummary] = useState<ProcessedMailSummary | null>(null);
  const [accounts, setAccounts] = useState<MailAccount[]>([]);
  const [status, setStatus] = useState<ProcessedMailStatus | "">("");
  const [account, setAccount] = useState<number | "">("");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const query = useMemo(
    () => ({ q: q.trim(), status, account }),
    [q, status, account],
  );

  function load() {
    setLoading(true);
    setError(null);
    Promise.all([
      getProcessedMails(query),
      getProcessedMailSummary(),
      getMailAccounts(),
    ])
      .then(([mails, nextSummary, nextAccounts]) => {
        setItems(mails.results);
        setSummary(nextSummary);
        setAccounts(nextAccounts);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (tab === "center") load();
  }, [tab, query]);

  return (
    <section className="mail-center">
      <div className="mail-center-tabs" role="tablist" aria-label="E-Mail-Zentrum">
        <button
          type="button"
          className={tab === "center" ? "active" : ""}
          onClick={() => setTab("center")}
        >
          Eingang
        </button>
        <button
          type="button"
          className={tab === "accounts" ? "active" : ""}
          onClick={() => setTab("accounts")}
        >
          Konten
        </button>
      </div>

      {tab === "accounts" ? (
        <MailAccountsAdmin canEdit={canEdit} />
      ) : (
        <>
          {summary && (
            <div className="mail-center-metrics">
              <Metric label="gesamt" value={summary.total} />
              <Metric label="importiert" value={summary.imported} />
              <Metric label="teilweise" value={summary.partial} />
              <Metric label="ignoriert" value={summary.ignored} />
              <Metric label="fehlerhaft" value={summary.failed} />
              <Metric label="Dokumente" value={summary.attachments} />
            </div>
          )}

          <div className="mail-center-toolbar">
            <input
              className="search"
              placeholder="Mail suchen …"
              value={q}
              onChange={(event) => setQ(event.target.value)}
            />
            <select
              value={status}
              onChange={(event) =>
                setStatus(event.target.value as ProcessedMailStatus | "")
              }
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value || "all"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select
              value={account}
              onChange={(event) =>
                setAccount(event.target.value ? Number(event.target.value) : "")
              }
            >
              <option value="">Alle Konten</option>
              {accounts.map((mailAccount) => (
                <option key={mailAccount.id} value={mailAccount.id}>
                  {mailAccount.name}
                </option>
              ))}
            </select>
            <button type="button" className="link" onClick={load}>
              Aktualisieren
            </button>
          </div>

          {loading && <p className="muted">Lade Mails …</p>}
          {error && (
            <div className="state state--error">
              <strong>Mails konnten nicht geladen werden.</strong>
              <span>{error}</span>
            </div>
          )}
          {!loading && !error && items.length === 0 && (
            <div className="state">
              <strong>Keine Mails gefunden.</strong>
              <span>Verarbeitete IMAP-Mails erscheinen hier nach dem nächsten Abruf.</span>
            </div>
          )}
          {!loading && !error && items.length > 0 && (
            <div className="mail-center-list">
              {items.map((item) => (
                <MailRow
                  key={item.id}
                  item={item}
                  canEdit={canEdit}
                  onOpenDocument={onOpenDocument}
                  onChanged={load}
                />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
