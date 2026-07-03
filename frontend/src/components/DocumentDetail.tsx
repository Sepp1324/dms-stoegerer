import { useEffect, useRef, useState } from "react";
import {
  addDocumentVersion,
  applySuggestions,
  dismissSuggestions,
  getDocument,
  getDocumentAudit,
  getDocumentIntegrity,
  getDocumentPreview,
  getDocumentVersionFile,
  suggestDocument,
  updateDocument,
  type AuditEntry,
  type DocumentDetail as Detail,
  type DocumentIntegrity,
  type DocumentVersion,
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
  // Versionen: aktuell in der Vorschau angezeigte Versionsnummer + Integritätsprüfung.
  const [selectedVersionNo, setSelectedVersionNo] = useState<number | null>(null);
  const [integrity, setIntegrity] = useState<DocumentIntegrity | null>(null);
  const [integrityError, setIntegrityError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
      .then((d) => {
        if (!active) return;
        setDoc(d);
        // Vorschau standardmäßig auf die neueste Version stellen.
        const newest = d.versions.reduce(
          (max, v) => Math.max(max, v.version_no),
          0,
        );
        setSelectedVersionNo(newest || null);
      })
      .catch((e) => active && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
  }, [id, refresh]);

  // Integritätsprüfung der Hash-Kette (rechnet Datei-Hashes serverseitig nach).
  useEffect(() => {
    let active = true;
    setIntegrity(null);
    setIntegrityError(null);
    getDocumentIntegrity(id)
      .then((r) => active && setIntegrity(r))
      .catch((e) => active && setIntegrityError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
  }, [id, refresh]);

  useEffect(() => {
    if (selectedVersionNo === null) return;
    let url: string | null = null;
    let active = true;
    setPdfUrl(null);
    setPdfError(null);
    getDocumentPreview(id, selectedVersionNo)
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
  }, [id, selectedVersionNo]);

  async function onAddVersion(file: File) {
    setAddBusy(true);
    setAddError(null);
    try {
      const updated = await addDocumentVersion(id, file);
      setDoc(updated);
      const newest = updated.versions.reduce(
        (max, v) => Math.max(max, v.version_no),
        0,
      );
      setSelectedVersionNo(newest || null);
      setRefresh((r) => r + 1); // Integrität neu prüfen
    } catch (e) {
      setAddError(e instanceof Error ? e.message : String(e));
    } finally {
      setAddBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function downloadVersion(versionNo: number) {
    try {
      const blob = await getDocumentVersionFile(id, versionNo);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${doc?.title ?? "dokument"}-v${versionNo}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setAddError(e instanceof Error ? e.message : String(e));
    }
  }

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

  async function dismiss(field: string) {
    setApplying(true);
    setApplyError(null);
    try {
      const updated = await dismissSuggestions(id, [field]);
      setDoc(updated);
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : String(e));
    } finally {
      setApplying(false);
    }
  }

  // Regeneriert die KI-Vorschläge synchron; bei fehlendem Provider Hinweis anzeigen.
  const [regenerating, setRegenerating] = useState(false);
  const [regenNote, setRegenNote] = useState<string | null>(null);

  async function regenerate() {
    setRegenerating(true);
    setRegenNote(null);
    setApplyError(null);
    try {
      const updated = await suggestDocument(id);
      const { source, ...rest } = updated;
      setDoc(rest as Detail);
      if (source === "unavailable") {
        setRegenNote("KI nicht verfügbar – es wurden keine Vorschläge erzeugt.");
      }
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : String(e));
    } finally {
      setRegenerating(false);
    }
  }

  const versions = [...(doc?.versions ?? [])].sort(
    (a, b) => b.version_no - a.version_no,
  );

  const s = doc?.ai_suggestions ?? {};
  const suggestionRows: { key: string; label: string; value: string }[] = [];
  // Belegdatum zuerst und hervorgehoben (ISO YYYY-MM-DD → de-DE); Übernehmen setzt created_at.
  if (s.date)
    suggestionRows.push({ key: "date", label: "Belegdatum", value: formatIsoDate(s.date) });
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
                {canEdit && (
                  <div className="ai-panel">
                    <div className="ai-panel__head">
                      <span>
                        <i aria-hidden="true">✦</i> KI-Vorschläge
                      </span>
                      <div className="ai-panel__actions">
                        <button
                          className="link"
                          onClick={regenerate}
                          disabled={regenerating || applying}
                        >
                          {regenerating ? "Generiere …" : "Neu generieren"}
                        </button>
                        {suggestionRows.length > 0 && (
                          <button onClick={() => apply()} disabled={applying || regenerating}>
                            {applying ? "…" : "Alle übernehmen"}
                          </button>
                        )}
                      </div>
                    </div>
                    {s.summary && (
                      <div className="ai-panel__summary-row">
                        <p className="ai-panel__summary">{s.summary}</p>
                        <button
                          className="link ai-suggestions__dismiss"
                          onClick={() => dismiss("summary")}
                          disabled={applying || regenerating}
                          title="Zusammenfassung verwerfen"
                        >
                          Verwerfen
                        </button>
                      </div>
                    )}
                    {suggestionRows.length > 0 ? (
                      <ul className="ai-suggestions">
                        {suggestionRows.map((row) => (
                          <li key={row.key}>
                            <span className="ai-suggestions__label">{row.label}</span>
                            <span className="ai-suggestions__value">{row.value}</span>
                            <button
                              className="link"
                              onClick={() => apply([row.key])}
                              disabled={applying || regenerating}
                            >
                              Übernehmen
                            </button>
                            <button
                              className="link ai-suggestions__dismiss"
                              onClick={() => dismiss(row.key)}
                              disabled={applying || regenerating}
                              title={`${row.label} verwerfen`}
                            >
                              Verwerfen
                            </button>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      !s.summary && (
                        <p className="muted ai-panel__empty">
                          Keine KI-Vorschläge vorhanden.
                        </p>
                      )
                    )}
                    {regenNote && <p className="status status--warn">{regenNote}</p>}
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

                <VersionsPanel
                  versions={versions}
                  currentVersionId={doc.current_version}
                  selectedVersionNo={selectedVersionNo}
                  onSelect={setSelectedVersionNo}
                  onDownload={downloadVersion}
                  integrity={integrity}
                  integrityError={integrityError}
                  canEdit={canEdit}
                  addBusy={addBusy}
                  addError={addError}
                  fileInputRef={fileInputRef}
                  onAddVersion={onAddVersion}
                />
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

type IntegrityStatus = "ok" | "broken" | "unknown";

function IntegrityBadge({ status }: { status: IntegrityStatus }) {
  const label =
    status === "ok" ? "Integrität ok" : status === "broken" ? "Integrität gebrochen" : "Prüfe …";
  const icon = status === "ok" ? "✓" : status === "broken" ? "⚠" : "…";
  return (
    <span className={`integrity-badge integrity-badge--${status}`} title={label}>
      <i aria-hidden="true">{icon}</i> {label}
    </span>
  );
}

function VersionsPanel({
  versions,
  currentVersionId,
  selectedVersionNo,
  onSelect,
  onDownload,
  integrity,
  integrityError,
  canEdit,
  addBusy,
  addError,
  fileInputRef,
  onAddVersion,
}: {
  versions: DocumentVersion[];
  currentVersionId: number | null;
  selectedVersionNo: number | null;
  onSelect: (versionNo: number) => void;
  onDownload: (versionNo: number) => void;
  integrity: DocumentIntegrity | null;
  integrityError: string | null;
  canEdit: boolean;
  addBusy: boolean;
  addError: string | null;
  fileInputRef: { current: HTMLInputElement | null };
  onAddVersion: (file: File) => void;
}) {
  // Integritätsergebnis je Versionsnummer nachschlagbar machen.
  const byNo = new Map(integrity?.versions.map((v) => [v.version_no, v]) ?? []);
  const overall: IntegrityStatus = integrityError
    ? "broken"
    : integrity === null
      ? "unknown"
      : integrity.chain_ok
        ? "ok"
        : "broken";

  function statusFor(versionNo: number): IntegrityStatus {
    const info = byNo.get(versionNo);
    if (!info) return "unknown";
    return info.file_ok && info.prev_ok ? "ok" : "broken";
  }

  return (
    <div className="version-info versions-panel">
      <div className="versions-panel__head">
        <h3>Versionen ({versions.length})</h3>
        <IntegrityBadge status={overall} />
      </div>
      {integrityError && (
        <p className="status status--warn">Integritätsprüfung: {integrityError}</p>
      )}

      <ul className="version-list">
        {versions.map((v) => {
          const st = statusFor(v.version_no);
          const info = byNo.get(v.version_no);
          const isSelected = v.version_no === selectedVersionNo;
          return (
            <li
              key={v.id}
              className={`version-row ${isSelected ? "version-row--selected" : ""}`}
            >
              <div className="version-row__top">
                <span className="version-row__no">
                  v{v.version_no}
                  {v.id === currentVersionId && (
                    <span className="version-row__current">aktuell</span>
                  )}
                </span>
                <IntegrityBadge status={st} />
              </div>
              <dl className="version-row__meta">
                <dt>Datum</dt>
                <dd>{new Date(v.created_at).toLocaleString("de-DE")}</dd>
                <dt>Ersteller</dt>
                <dd>{v.created_by_name ?? "—"}</dd>
                <dt>Größe</dt>
                <dd>{formatBytes(v.size)}</dd>
                <dt>Seiten</dt>
                <dd>{v.page_count ?? "—"}</dd>
                <dt>SHA-256</dt>
                <dd className="mono version-row__hash">{v.sha256 || "— (in Arbeit)"}</dd>
                <dt>Vorgänger-Hash</dt>
                <dd className="mono version-row__hash">
                  {v.prev_hash || "— (erste Version)"}
                </dd>
              </dl>
              {st === "broken" && info && (
                <p className="status status--error version-row__warn">
                  {!info.file_present
                    ? "Datei fehlt auf der Ablage."
                    : !info.file_ok
                      ? "Datei-Hash weicht ab – Inhalt verändert."
                      : "Vorgänger-Hash passt nicht – Kette unterbrochen."}
                </p>
              )}
              <div className="version-row__actions">
                <button
                  type="button"
                  className="link"
                  disabled={isSelected}
                  onClick={() => onSelect(v.version_no)}
                >
                  {isSelected ? "In Vorschau" : "Vorschau"}
                </button>
                <button
                  type="button"
                  className="link"
                  onClick={() => onDownload(v.version_no)}
                >
                  Download
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      {canEdit && (
        <div className="version-add">
          <input
            ref={fileInputRef}
            type="file"
            disabled={addBusy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onAddVersion(file);
            }}
          />
          <span className="muted">
            {addBusy ? "Lade neue Version hoch …" : "Neue Version zu diesem Dokument hinzufügen"}
          </span>
          {addError && <p className="status status--error">{addError}</p>}
        </div>
      )}
    </div>
  );
}

// ISO-Belegdatum (YYYY-MM-DD) menschenlesbar; ungültige Werte unverändert lassen.
function formatIsoDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("de-DE", {
    day: "2-digit",
    month: "long",
    year: "numeric",
  });
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
