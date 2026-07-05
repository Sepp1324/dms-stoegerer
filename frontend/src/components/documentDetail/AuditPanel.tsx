import { useEffect, useState } from "react";
import { getDocumentAudit, type AuditEntry } from "../../api";

// Menschlich lesbare Bezeichnungen für Aktionen und Felder.
const ACTION_LABELS: Record<string, string> = {
  upload: "Upload / Erstellung",
  add_version: "Neue Version",
  ocr: "Texterkennung (OCR)",
  classify: "Automatische Klassifizierung",
  update: "Metadaten geändert",
  apply_suggestions: "KI-Vorschläge übernommen",
  submit: "Zur Freigabe eingereicht",
  approve: "Freigegeben",
  reject: "Abgelehnt",
  delete: "Gelöscht",
};
const FIELD_LABELS: Record<string, string> = {
  title: "Titel",
  correspondent: "Korrespondent",
  document_type: "Typ",
  storage_path: "Ablagepfad",
  tags: "Schlagworte",
};

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  return String(value);
}

// Kompakte, aktionsabhängige Zusammenfassung des Audit-Details.
function AuditDetail({ entry }: { entry: AuditEntry }) {
  const detail = entry.detail || {};

  if (entry.action === "update" && detail.changes) {
    const changes = detail.changes as Record<
      string,
      { from: unknown; to: unknown }
    >;
    return (
      <ul className="audit-changes">
        {Object.entries(changes).map(([field, { from, to }]) => (
          <li key={field}>
            <span className="audit-changes__field">
              {FIELD_LABELS[field] ?? field}
            </span>
            <span className="audit-changes__from">{formatValue(from)}</span>
            <span aria-hidden="true">→</span>
            <span className="audit-changes__to">{formatValue(to)}</span>
          </li>
        ))}
      </ul>
    );
  }

  const parts: string[] = [];
  if (entry.action === "apply_suggestions" && Array.isArray(detail.fields)) {
    parts.push(
      "Felder: " +
        (detail.fields as string[]).map((f) => FIELD_LABELS[f] ?? f).join(", "),
    );
  }
  if (entry.action === "classify" && Array.isArray(detail.rules)) {
    parts.push("Regeln: " + formatValue(detail.rules));
  }
  if (entry.action === "ocr" && detail.pages != null) {
    parts.push(`${detail.pages} Seite(n) erkannt`);
  }
  if ((entry.action === "upload" || entry.action === "delete") && detail.title) {
    parts.push(String(detail.title));
  }
  if (entry.action === "add_version" && detail.version_no != null) {
    parts.push(`Version ${detail.version_no}`);
  }
  if (
    (entry.action === "upload" || entry.action === "add_version") &&
    detail.filename
  ) {
    parts.push(String(detail.filename));
  }
  if (entry.object_type === "DocumentVersion") {
    parts.push("Version");
  }

  if (!parts.length) return null;
  return <p className="audit-detail">{parts.join(" · ")}</p>;
}

export function AuditTrail({ id }: { id: number }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setErr(null);
    getDocumentAudit(id, 1)
      .then((res) => {
        if (!active) return;
        setEntries(res.results);
        setCount(res.count);
        setHasNext(!!res.next);
        setPage(1);
      })
      .catch((e) => active && setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [id]);

  async function loadMore() {
    setLoading(true);
    setErr(null);
    try {
      const next = page + 1;
      const res = await getDocumentAudit(id, next);
      setEntries((prev) => [...prev, ...res.results]);
      setHasNext(!!res.next);
      setPage(next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="audit">
      <h3>Verlauf {count > 0 && <span className="muted">({count})</span>}</h3>
      {err && <p className="status status--error">{err}</p>}
      {!err && !loading && entries.length === 0 && (
        <p className="muted">Noch keine Ereignisse protokolliert.</p>
      )}
      <ol className="audit-list">
        {entries.map((e) => (
          <li key={e.id} className="audit-item">
            <div className="audit-item__head">
              <span className="audit-item__action">
                {ACTION_LABELS[e.action] ?? e.action}
              </span>
              <time className="audit-item__time" dateTime={e.timestamp}>
                {new Date(e.timestamp).toLocaleString("de-DE")}
              </time>
            </div>
            <div className="audit-item__actor">{e.actor_name}</div>
            <AuditDetail entry={e} />
          </li>
        ))}
      </ol>
      {loading && <p className="muted">Lade Verlauf …</p>}
      {hasNext && !loading && (
        <button className="link" onClick={loadMore}>
          Mehr laden
        </button>
      )}
    </div>
  );
}
