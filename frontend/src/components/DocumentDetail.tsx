import { useEffect, useState } from "react";
import {
  applySuggestions,
  getDocument,
  getDocumentAudit,
  getDocumentPreview,
  updateDocument,
  type AuditEntry,
  type DocumentDetail as Detail,
  type NamedRef,
} from "../api";

interface Props {
  id: number;
  onBack: () => void;
  correspondents: NamedRef[];
  documentTypes: NamedRef[];
  storagePaths: NamedRef[];
  allTags: NamedRef[];
  canEdit: boolean;
  onCreateCorrespondent: (name: string) => Promise<NamedRef>;
  onCreateDocumentType: (name: string) => Promise<NamedRef>;
  onCreateStoragePath: (name: string) => Promise<NamedRef>;
  onCreateTag: (name: string) => Promise<NamedRef>;
}

export default function DocumentDetail({
  id,
  onBack,
  correspondents,
  documentTypes,
  storagePaths,
  allTags,
  canEdit,
  onCreateCorrespondent,
  onCreateDocumentType,
  onCreateStoragePath,
  onCreateTag,
}: Props) {
  const [doc, setDoc] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);

  const [tab, setTab] = useState<"details" | "history">("details");

  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [form, setForm] = useState({
    title: "",
    correspondent: "" as number | "",
    document_type: "" as number | "",
    storage_path: "" as number | "",
    tagIds: new Set<number>(),
  });

  useEffect(() => {
    let active = true;
    getDocument(id)
      .then((d) => active && setDoc(d))
      .catch((e) => active && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
  }, [id]);

  useEffect(() => {
    let url: string | null = null;
    let active = true;
    getDocumentPreview(id)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setPdfUrl(url);
      })
      .catch((e) => active && setPdfError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [id]);

  function startEdit() {
    if (!doc) return;
    setForm({
      title: doc.title,
      correspondent: doc.correspondent ?? "",
      document_type: doc.document_type ?? "",
      storage_path: doc.storage_path ?? "",
      tagIds: new Set(doc.tags.map((t) => t.id)),
    });
    setSaveError(null);
    setEditing(true);
  }

  function toggleTag(tagId: number) {
    setForm((f) => {
      const next = new Set(f.tagIds);
      next.has(tagId) ? next.delete(tagId) : next.add(tagId);
      return { ...f, tagIds: next };
    });
  }

  async function save() {
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await updateDocument(id, {
        title: form.title,
        correspondent: form.correspondent === "" ? null : form.correspondent,
        document_type: form.document_type === "" ? null : form.document_type,
        storage_path: form.storage_path === "" ? null : form.storage_path,
        tag_ids: Array.from(form.tagIds),
      });
      setDoc(updated);
      setEditing(false);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);

  async function apply(fields?: string[]) {
    setApplying(true);
    setApplyError(null);
    try {
      const updated = await applySuggestions(id, fields);
      setDoc(updated);
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : String(e));
    } finally {
      setApplying(false);
    }
  }

  const versions = doc?.versions ?? [];
  const version =
    versions.find((v) => v.id === doc?.current_version) ?? versions[versions.length - 1];

  const s = doc?.ai_suggestions ?? {};
  const suggestionRows: { key: string; label: string; value: string }[] = [];
  if (s.title) suggestionRows.push({ key: "title", label: "Titel", value: s.title });
  if (s.correspondent)
    suggestionRows.push({ key: "correspondent", label: "Korrespondent", value: s.correspondent });
  if (s.document_type)
    suggestionRows.push({ key: "document_type", label: "Typ", value: s.document_type });
  if (s.tags && s.tags.length)
    suggestionRows.push({ key: "tags", label: "Schlagworte", value: s.tags.join(", ") });

  return (
    <div className="shell">
      <header className="topbar">
        <button className="link" onClick={onBack}>
          ← Zurück zur Liste
        </button>
        {doc && canEdit && !editing && <button onClick={startEdit}>Bearbeiten</button>}
      </header>

      {error && <p className="status status--error">{error}</p>}
      {!doc && !error && <p className="muted">Lade …</p>}

      {doc && (
        <div className="detail">
          <section className="card detail-meta">
            {editing ? (
              <div className="edit-form">
                <label>
                  Titel
                  <input
                    value={form.title}
                    onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                  />
                </label>

                <CreatableSelect
                  label="Korrespondent"
                  value={form.correspondent}
                  onChange={(v) => setForm((f) => ({ ...f, correspondent: v }))}
                  options={correspondents}
                  onCreate={onCreateCorrespondent}
                />
                <CreatableSelect
                  label="Typ"
                  value={form.document_type}
                  onChange={(v) => setForm((f) => ({ ...f, document_type: v }))}
                  options={documentTypes}
                  onCreate={onCreateDocumentType}
                />
                <CreatableSelect
                  label="Ablagepfad"
                  value={form.storage_path}
                  onChange={(v) => setForm((f) => ({ ...f, storage_path: v }))}
                  options={storagePaths}
                  onCreate={onCreateStoragePath}
                />

                <div className="edit-tags">
                  <span className="edit-tags__label">Schlagworte</span>
                  <div className="tag-toggle-list">
                    {allTags.map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        className={`tag tag-toggle ${form.tagIds.has(t.id) ? "tag-toggle--on" : ""}`}
                        onClick={() => toggleTag(t.id)}
                      >
                        {t.name}
                      </button>
                    ))}
                  </div>
                  <InlineCreate
                    placeholder="Neues Schlagwort"
                    buttonLabel="+ Tag"
                    onCreate={async (name) => {
                      const item = await onCreateTag(name);
                      toggleTag(item.id);
                    }}
                  />
                </div>

                {saveError && <p className="status status--error">{saveError}</p>}
                <div className="edit-actions">
                  <button onClick={save} disabled={saving || !form.title.trim()}>
                    {saving ? "Speichern …" : "Speichern"}
                  </button>
                  <button className="link" onClick={() => setEditing(false)} disabled={saving}>
                    Abbrechen
                  </button>
                </div>
              </div>
            ) : tab === "history" ? (
              <>
                <DetailTabs tab={tab} onChange={setTab} />
                <AuditTrail id={id} />
              </>
            ) : (
              <>
                <DetailTabs tab={tab} onChange={setTab} />
                {canEdit && suggestionRows.length > 0 && (
                  <div className="ai-panel">
                    <div className="ai-panel__head">
                      <span>
                        <i aria-hidden="true">✦</i> KI-Vorschläge
                      </span>
                      <button onClick={() => apply()} disabled={applying}>
                        {applying ? "…" : "Alle übernehmen"}
                      </button>
                    </div>
                    {s.summary && <p className="ai-panel__summary">{s.summary}</p>}
                    <ul className="ai-suggestions">
                      {suggestionRows.map((row) => (
                        <li key={row.key}>
                          <span className="ai-suggestions__label">{row.label}</span>
                          <span className="ai-suggestions__value">{row.value}</span>
                          <button
                            className="link"
                            onClick={() => apply([row.key])}
                            disabled={applying}
                          >
                            Übernehmen
                          </button>
                        </li>
                      ))}
                    </ul>
                    {applyError && <p className="status status--error">{applyError}</p>}
                  </div>
                )}

                <h2>{doc.title}</h2>
                {doc.classification?.rules?.length ? (
                  <p className="class-note">
                    <i aria-hidden="true">⚙</i> Automatisch klassifiziert durch Regel
                    {doc.classification.rules.length > 1 ? "n" : ""}{" "}
                    „{doc.classification.rules.join("“, „")}“
                  </p>
                ) : null}
                <dl>
                  <dt>Korrespondent</dt>
                  <dd>{doc.correspondent_name ?? "—"}</dd>
                  <dt>Typ</dt>
                  <dd>{doc.document_type_name ?? "—"}</dd>
                  <dt>Ablagepfad</dt>
                  <dd>{doc.storage_path_name ?? "—"}</dd>
                  <dt>Aufgenommen</dt>
                  <dd>{new Date(doc.added_at).toLocaleString("de-DE")}</dd>
                  <dt>Seiten</dt>
                  <dd>{doc.page_count ?? "—"}</dd>
                  <dt>Schlagworte</dt>
                  <dd>
                    {doc.tags.length > 0
                      ? doc.tags.map((t) => (
                          <span key={t.id} className="tag" style={{ borderColor: t.color, color: t.color }}>
                            {t.name}
                          </span>
                        ))
                      : "—"}
                  </dd>
                </dl>

                {version && (
                  <div className="version-info">
                    <h3>Version {version.version_no}</h3>
                    <dl>
                      <dt>SHA-256</dt>
                      <dd className="mono">{version.sha256 || "—"}</dd>
                      <dt>Vorgänger-Hash</dt>
                      <dd className="mono">{version.prev_hash || "— (erste Version)"}</dd>
                      <dt>Größe</dt>
                      <dd>{formatBytes(version.size)}</dd>
                    </dl>
                  </div>
                )}
              </>
            )}
          </section>

          <section className="card detail-preview">
            {pdfError && <p className="status status--warn">Vorschau: {pdfError}</p>}
            {!pdfError && !pdfUrl && <p className="muted">Lade Vorschau …</p>}
            {pdfUrl && (
              <iframe className="pdf-frame" src={pdfUrl} title={`Vorschau: ${doc.title}`} />
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function CreatableSelect({
  label,
  value,
  onChange,
  options,
  onCreate,
}: {
  label: string;
  value: number | "";
  onChange: (v: number | "") => void;
  options: NamedRef[];
  onCreate: (name: string) => Promise<NamedRef>;
}) {
  const [adding, setAdding] = useState(false);
  return (
    <label>
      {label}
      <div className="creatable">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value ? Number(e.target.value) : "")}
        >
          <option value="">— keiner —</option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
        <button type="button" className="link" onClick={() => setAdding((a) => !a)}>
          + neu
        </button>
      </div>
      {adding && (
        <InlineCreate
          placeholder="Name"
          buttonLabel="Anlegen"
          onCreate={async (name) => {
            const item = await onCreate(name);
            onChange(item.id);
            setAdding(false);
          }}
        />
      )}
    </label>
  );
}

function InlineCreate({
  placeholder,
  buttonLabel,
  onCreate,
}: {
  placeholder: string;
  buttonLabel: string;
  onCreate: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await onCreate(name.trim());
      setName("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="creatable-new">
      <div style={{ display: "flex", gap: "0.4rem" }}>
        <input
          value={name}
          placeholder={placeholder}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              create();
            }
          }}
        />
        <button type="button" onClick={create} disabled={busy || !name.trim()}>
          {busy ? "…" : buttonLabel}
        </button>
      </div>
      {err && <span className="status status--error">{err}</span>}
    </div>
  );
}

function DetailTabs({
  tab,
  onChange,
}: {
  tab: "details" | "history";
  onChange: (t: "details" | "history") => void;
}) {
  return (
    <div className="detail-tabs" role="tablist">
      <button
        role="tab"
        aria-selected={tab === "details"}
        className={`detail-tab ${tab === "details" ? "detail-tab--active" : ""}`}
        onClick={() => onChange("details")}
      >
        Details
      </button>
      <button
        role="tab"
        aria-selected={tab === "history"}
        className={`detail-tab ${tab === "history" ? "detail-tab--active" : ""}`}
        onClick={() => onChange("history")}
      >
        Verlauf
      </button>
    </div>
  );
}

// Menschlich lesbare Bezeichnungen für Aktionen und Felder.
const ACTION_LABELS: Record<string, string> = {
  upload: "Upload / Erstellung",
  add_version: "Neue Version",
  ocr: "Texterkennung (OCR)",
  classify: "Automatische Klassifizierung",
  update: "Metadaten geändert",
  apply_suggestions: "KI-Vorschläge übernommen",
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

function AuditTrail({ id }: { id: number }) {
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

function formatBytes(bytes: number): string {
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}
