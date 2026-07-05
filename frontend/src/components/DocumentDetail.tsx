import { useEffect, useRef, useState } from "react";
import {
  addDocumentVersion,
  applySuggestions,
  approveDocument,
  compareVersions,
  createShareLink,
  dismissSuggestions,
  getDocument,
  getDocumentAudit,
  getDocumentIntegrity,
  getDocumentPreview,
  getDocumentQr,
  getDocumentVersionFile,
  getShareLinks,
  rejectDocument,
  retryProcessing,
  revokeShareLink,
  submitDocument,
  suggestDocument,
  updateDocument,
  type AuditEntry,
  type CompareFieldChange,
  type CompareSectionDiff,
  type CustomField,
  type CustomFieldValue,
  type DocumentDetail as Detail,
  type DocumentIntegrity,
  type DocumentStatus,
  type DocumentVersion,
  type NamedRef,
  type ShareLink,
  type VersionCompare,
} from "../api";
import { sanitizeDiffHtml } from "../sanitize";
import {
  formatCustomFieldValue,
  toCanonicalValue,
  toInputValue,
} from "../customFields";
import {
  ProcessingBadge,
  ocrStatusLabel,
  processingStateLabel,
} from "./ProcessingStatus";

interface Props {
  id: number;
  onBack: () => void;
  correspondents: NamedRef[];
  documentTypes: NamedRef[];
  storagePaths: NamedRef[];
  allTags: NamedRef[];
  // Globale Zusatzfeld-Definitionen (STOAA-113) – werden in DocumentsPage einmal
  // geladen und hier zur Anzeige aller Felder (auch ohne Wert) durchgereicht.
  customFields: CustomField[];
  canEdit: boolean;
  onCreateCorrespondent: (name: string) => Promise<NamedRef>;
  onCreateDocumentType: (name: string) => Promise<NamedRef>;
  onCreateStoragePath: (name: string) => Promise<NamedRef>;
  onCreateTag: (name: string) => Promise<NamedRef>;
  // Navigation zur SPA-Verwaltung der Zusatzfelder (Empty-State-Link).
  onManageFields?: () => void;
}

export default function DocumentDetail({
  id,
  onBack,
  correspondents,
  documentTypes,
  storagePaths,
  allTags,
  customFields,
  canEdit,
  onCreateCorrespondent,
  onCreateDocumentType,
  onCreateStoragePath,
  onCreateTag,
  onManageFields,
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
  // Retry der Dokumentverarbeitung (STOAA-249): nur bei processing_state=failed.
  const [retryBusy, setRetryBusy] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
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

  // Verarbeitung der aktuellen Version neu anstoßen und danach das Detail neu
  // laden (der ``refresh``-Tick triggert getDocument + Integritätsprüfung).
  async function onRetry() {
    setRetryBusy(true);
    setRetryError(null);
    try {
      await retryProcessing(id);
      setRefresh((r) => r + 1);
    } catch (e) {
      setRetryError(e instanceof Error ? e.message : String(e));
    } finally {
      setRetryBusy(false);
    }
  }

  // Lädt den ASN-QR-Code als PNG herunter (STOAA-286). Dateiname = ASN-Label,
  // damit das gedruckte Label eindeutig dem Dokument zuzuordnen ist.
  async function downloadQr() {
    try {
      const blob = await getDocumentQr(id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${doc?.asn_label ?? "asn"}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setAddError(e instanceof Error ? e.message : String(e));
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

  // Zusatzfelder speichern (STOAA-113): Upsert-Liste als PATCH; Antwort ersetzt
  // das Dokument (inkl. aktualisierter custom_field_values).
  async function saveCustomFields(
    values: { field: number; value: string }[],
  ): Promise<void> {
    const updated = await updateDocument(id, { custom_field_values: values });
    setDoc(updated);
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

  // Freigabe-Workflow (Stufe 4): eine Aktion aktiv, Fehler separat anzeigen.
  const [freigabeBusy, setFreigabeBusy] = useState(false);
  const [freigabeError, setFreigabeError] = useState<string | null>(null);

  // Führt eine Freigabe-Aktion aus und lädt danach Dokument + Verlauf/Integrität neu.
  async function runFreigabe(action: () => Promise<Detail>) {
    setFreigabeBusy(true);
    setFreigabeError(null);
    try {
      const updated = await action();
      setDoc(updated);
      setRefresh((r) => r + 1); // Verlauf & Integrität neu laden
    } catch (e) {
      setFreigabeError(e instanceof Error ? e.message : String(e));
    } finally {
      setFreigabeBusy(false);
    }
  }

  const versions = [...(doc?.versions ?? [])].sort(
    (a, b) => b.version_no - a.version_no,
  );
  // Aktuelle Version für das Verarbeitungs-Widget (STOAA-249). Der Rollup
  // ``doc.processing_state`` spiegelt genau diese Version; das Widget zeigt
  // zusätzlich Fehlerdetails/Versuche aus dem vollen Versionsobjekt.
  const currentVersion = versions.find((v) => v.id === doc?.current_version);

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
                <FreigabePanel
                  status={doc.status}
                  canEdit={canEdit}
                  busy={freigabeBusy}
                  error={freigabeError}
                  onSubmit={() => runFreigabe(() => submitDocument(id))}
                  onApprove={() => runFreigabe(() => approveDocument(id))}
                  onReject={(reason) => runFreigabe(() => rejectDocument(id, reason))}
                />
                <ProcessingPanel
                  version={currentVersion}
                  canEdit={canEdit}
                  retryBusy={retryBusy}
                  retryError={retryError}
                  onRetry={onRetry}
                />
                {doc.classification?.rules?.length ? (
                  <p className="class-note">
                    <i aria-hidden="true">⚙</i> Automatisch klassifiziert durch Regel
                    {doc.classification.rules.length > 1 ? "n" : ""}{" "}
                    „{doc.classification.rules.join("“, „")}“
                  </p>
                ) : null}
                <dl>
                  <dt>Archivnummer</dt>
                  <dd className="asn">
                    {doc.asn_label ? (
                      <>
                        <span className="asn__value">{doc.asn_label}</span>
                        <button
                          type="button"
                          className="link"
                          onClick={downloadQr}
                        >
                          QR-Code herunterladen
                        </button>
                      </>
                    ) : (
                      "—"
                    )}
                  </dd>
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

                <CustomFieldsPanel
                  fields={customFields}
                  values={doc.custom_field_values ?? []}
                  canEdit={canEdit}
                  onSave={saveCustomFields}
                  onManageFields={onManageFields}
                />

                <ShareLinksPanel documentId={id} canEdit={canEdit} />

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

                <ComparePanel
                  documentId={id}
                  versions={versions}
                  onDownload={downloadVersion}
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

// Verarbeitungs-Widget (STOAA-249): kompaktes Monitoring der aktuellen Version –
// processing_state, OCR-Status, fehlgeschlagener Schritt, letzter Fehlerzeitpunkt
// und Versuche. Fehlerdetails (processing_error/ocr_error) sind aufklappbar; der
// Retry-Button erscheint nur bei ``failed`` und Schreibrecht.
function ProcessingPanel({
  version,
  canEdit,
  retryBusy,
  retryError,
  onRetry,
}: {
  version: DocumentVersion | undefined;
  canEdit: boolean;
  retryBusy: boolean;
  retryError: string | null;
  onRetry: () => void;
}) {
  const [showErrors, setShowErrors] = useState(false);
  if (!version) return null;

  const state = version.processing_state ?? null;
  const isFailed = state === "failed";
  const hasErrorDetails = !!(version.processing_error || version.ocr_error);

  return (
    <div className="processing">
      <div className="processing__head">
        <span className="processing__label">Verarbeitung</span>
        <ProcessingBadge state={state} />
      </div>

      <dl className="processing__grid">
        <dt>Status</dt>
        <dd>{processingStateLabel(state)}</dd>
        <dt>OCR</dt>
        <dd>{ocrStatusLabel(version.ocr_status ?? null)}</dd>
        {version.processing_failed_step && (
          <>
            <dt>Fehlgeschlagener Schritt</dt>
            <dd>{version.processing_failed_step}</dd>
          </>
        )}
        {version.processing_failed_at && (
          <>
            <dt>Letzter Fehler</dt>
            <dd>{new Date(version.processing_failed_at).toLocaleString("de-DE")}</dd>
          </>
        )}
        <dt>Versuche</dt>
        <dd>{version.processing_attempts}</dd>
      </dl>

      {hasErrorDetails && (
        <div className="processing__errors">
          <button
            className="link processing__toggle"
            onClick={() => setShowErrors((v) => !v)}
            aria-expanded={showErrors}
          >
            {showErrors ? "Fehlerdetails ausblenden" : "Fehlerdetails anzeigen"}
          </button>
          {showErrors && (
            <div className="processing__error-body">
              {version.processing_error && (
                <div>
                  <p className="processing__error-title">Verarbeitungsfehler</p>
                  <pre className="processing__error-text">{version.processing_error}</pre>
                </div>
              )}
              {version.ocr_error && (
                <div>
                  <p className="processing__error-title">OCR-Fehler</p>
                  <pre className="processing__error-text">{version.ocr_error}</pre>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {isFailed && canEdit && (
        <button
          className="processing__retry"
          onClick={onRetry}
          disabled={retryBusy}
        >
          {retryBusy ? "Wird neu gestartet …" : "Verarbeitung erneut starten"}
        </button>
      )}
      {retryError && <p className="status status--error">{retryError}</p>}
    </div>
  );
}

// Deutsche Labels + Farbakzent (CSS-Modifier) je Freigabe-Status.
const STATUS_LABELS: Record<DocumentStatus, string> = {
  entwurf: "Entwurf",
  zur_freigabe: "Zur Freigabe",
  freigegeben: "Freigegeben",
  abgelehnt: "Abgelehnt",
};

// Statusanzeige + Freigabe-Buttons (Stufe 4). Buttons nur bei Schreibrecht und
// passend zum aktuellen Status; ``freigegeben`` bietet keine Aktion.
function FreigabePanel({
  status,
  canEdit,
  busy,
  error,
  onSubmit,
  onApprove,
  onReject,
}: {
  status: DocumentStatus;
  canEdit: boolean;
  busy: boolean;
  error: string | null;
  onSubmit: () => void;
  onApprove: () => void;
  onReject: (reason?: string) => void;
}) {
  // Ablehnen mit optionaler Begründung: erst Eingabe einblenden, dann bestätigen.
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  // Unbekannter/fehlender Status (z. B. vor BE-Merge): neutraler Fallback-Text.
  const label = STATUS_LABELS[status] ?? "Unbekannt";
  const accent = STATUS_LABELS[status] ? status : "unknown";

  function confirmReject() {
    onReject(reason.trim() || undefined);
    setRejecting(false);
    setReason("");
  }

  return (
    <div className="freigabe">
      <div className="freigabe__head">
        <span className="freigabe__label">Status</span>
        <span className={`freigabe-badge freigabe-badge--${accent}`}>{label}</span>
      </div>

      {canEdit && (
        <div className="freigabe__actions">
          {(status === "entwurf" || status === "abgelehnt") && (
            <button onClick={onSubmit} disabled={busy}>
              {busy ? "…" : "Zur Freigabe"}
            </button>
          )}
          {status === "zur_freigabe" && !rejecting && (
            <>
              <button onClick={onApprove} disabled={busy}>
                {busy ? "…" : "Genehmigen"}
              </button>
              <button
                className="freigabe__reject"
                onClick={() => setRejecting(true)}
                disabled={busy}
              >
                Ablehnen
              </button>
            </>
          )}
          {status === "zur_freigabe" && rejecting && (
            <div className="freigabe__reject-form">
              <input
                value={reason}
                placeholder="Begründung (optional)"
                onChange={(e) => setReason(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && confirmReject()}
                autoFocus
              />
              <button
                className="freigabe__reject"
                onClick={confirmReject}
                disabled={busy}
              >
                {busy ? "…" : "Ablehnen bestätigen"}
              </button>
              <button
                className="link"
                onClick={() => {
                  setRejecting(false);
                  setReason("");
                }}
                disabled={busy}
              >
                Abbrechen
              </button>
            </div>
          )}
        </div>
      )}

      {error && <p className="status status--error">{error}</p>}
    </div>
  );
}

// Ablauf-Schnellwahl (Tage) für neue Freigabelinks. Kein „nie" – Ablauf ist
// Pflicht (STOAA-192). Default 30 ist der mittlere, vorbelegte Wert.
const SHARE_EXPIRY_CHOICES = [7, 30, 90] as const;
const SHARE_EXPIRY_DEFAULT = 30;

// Leitet den Anzeige-Status eines Links ab. is_valid (Backend) = weder
// widerrufen noch abgelaufen; hier zusätzlich widerrufen ↔ abgelaufen getrennt.
type ShareLinkState = "gueltig" | "abgelaufen" | "widerrufen";
function shareLinkState(link: ShareLink): ShareLinkState {
  if (link.revoked_at) return "widerrufen";
  if (new Date(link.expires_at).getTime() <= Date.now()) return "abgelaufen";
  return "gueltig";
}
const SHARE_STATE_LABELS: Record<ShareLinkState, string> = {
  gueltig: "gültig",
  abgelaufen: "abgelaufen",
  widerrufen: "widerrufen",
};

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Freigabelinks-Sektion (STOAA-192): „Link teilen"-Dialog mit Pflicht-Ablauf
// (7/30/90 Tage, Default 30) + Link-Verwaltung je Dokument (Ablauf, Status,
// Widerruf). Nur bei Schreibrecht sichtbar – Gäste sehen die Sektion nicht.
// Der Klartext-Token kommt einmalig aus der Create-Response und wird direkt
// angezeigt + in die Zwischenablage kopiert. Die Aufruf-Seite /share/<token>
// ist NICHT Teil dieses Tickets (→ Ticket D).
function ShareLinksPanel({
  documentId,
  canEdit,
}: {
  documentId: number;
  canEdit: boolean;
}) {
  const [links, setLinks] = useState<ShareLink[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [days, setDays] = useState<number>(SHARE_EXPIRY_DEFAULT);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  // Nach erfolgreichem Create: Klartext-Link (einmalig) + Kopier-Status.
  const [created, setCreated] = useState<{ url: string; copied: boolean } | null>(
    null,
  );
  const [revokingId, setRevokingId] = useState<number | null>(null);

  // Nur bei Schreibrecht laden/anzeigen – Gäste haben ohnehin keinen Zugriff.
  useEffect(() => {
    if (!canEdit) return;
    let active = true;
    setLinks(null);
    setLoadError(null);
    getShareLinks(documentId)
      .then((rows) => active && setLinks(rows))
      .catch((e) => active && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
  }, [documentId, canEdit]);

  if (!canEdit) return null;

  function openDialog() {
    setDays(SHARE_EXPIRY_DEFAULT);
    setCreateError(null);
    setCreated(null);
    setDialogOpen(true);
  }

  async function submit() {
    setCreating(true);
    setCreateError(null);
    try {
      // Ablauf = jetzt + gewählte Tage (immer in der Zukunft → Backend-konform).
      const expiresAt = new Date(Date.now() + days * 86400000).toISOString();
      const link = await createShareLink(documentId, expiresAt);
      const url = `${window.location.origin}/share/${link.token}`;
      let copied = false;
      try {
        await navigator.clipboard.writeText(url);
        copied = true;
      } catch {
        // Zwischenablage evtl. gesperrt (kein HTTPS/Fokus) – Link bleibt sichtbar.
      }
      setCreated({ url, copied });
      // Liste aktualisieren (neuer Link erscheint, ohne Klartext-Token).
      setLinks((prev) => (prev ? [link, ...prev] : [link]));
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  async function copyAgain() {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.url);
      setCreated({ ...created, copied: true });
    } catch {
      /* Zwischenablage nicht verfügbar */
    }
  }

  async function revoke(id: number) {
    setRevokingId(id);
    try {
      const updated = await revokeShareLink(id);
      setLinks((prev) => prev?.map((l) => (l.id === id ? updated : l)) ?? null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <div className="share-links">
      <div className="share-links__head">
        <h3>Freigabelinks</h3>
        <button className="link" onClick={openDialog}>
          + Link teilen
        </button>
      </div>

      {loadError && <p className="status status--error">{loadError}</p>}
      {links === null && !loadError && <p className="muted">Lade …</p>}
      {links && links.length === 0 && (
        <p className="muted share-links__empty">Noch keine Freigabelinks.</p>
      )}
      {links && links.length > 0 && (
        <ul className="share-links__list">
          {links.map((link) => {
            const state = shareLinkState(link);
            return (
              <li key={link.id} className="share-links__row">
                <span className={`share-badge share-badge--${state}`}>
                  {SHARE_STATE_LABELS[state]}
                </span>
                <span className="share-links__expiry">
                  Ablauf: {formatDateTime(link.expires_at)}
                </span>
                {state === "gueltig" ? (
                  <button
                    className="share-links__revoke"
                    onClick={() => revoke(link.id)}
                    disabled={revokingId === link.id}
                  >
                    {revokingId === link.id ? "…" : "Widerrufen"}
                  </button>
                ) : (
                  <span />
                )}
              </li>
            );
          })}
        </ul>
      )}

      {dialogOpen && (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Link teilen"
          onClick={() => !creating && setDialogOpen(false)}
        >
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-card__head">
              <h3>Link teilen</h3>
              <button
                className="link"
                onClick={() => setDialogOpen(false)}
                disabled={creating}
                aria-label="Schließen"
              >
                ✕
              </button>
            </div>

            {!created ? (
              <>
                <p className="muted share-dialog__hint">
                  Der Link läuft automatisch ab – ein Ablaufdatum ist Pflicht.
                </p>
                <fieldset className="share-dialog__choices">
                  <legend className="share-dialog__legend">Gültig für</legend>
                  {SHARE_EXPIRY_CHOICES.map((choice) => (
                    <label
                      key={choice}
                      className={`share-choice ${days === choice ? "share-choice--on" : ""}`}
                    >
                      <input
                        type="radio"
                        name="share-expiry"
                        value={choice}
                        checked={days === choice}
                        onChange={() => setDays(choice)}
                      />
                      {choice} Tage
                    </label>
                  ))}
                </fieldset>
                {createError && <p className="status status--error">{createError}</p>}
                <div className="modal-card__actions">
                  <button onClick={submit} disabled={creating}>
                    {creating ? "Erstelle …" : "Link erstellen"}
                  </button>
                  <button
                    className="link"
                    onClick={() => setDialogOpen(false)}
                    disabled={creating}
                  >
                    Abbrechen
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="share-dialog__ok">
                  {created.copied
                    ? "Link erstellt und in die Zwischenablage kopiert."
                    : "Link erstellt. Bitte manuell kopieren:"}
                </p>
                <div className="share-dialog__link">
                  <input readOnly value={created.url} onFocus={(e) => e.target.select()} />
                  <button onClick={copyAgain}>Kopieren</button>
                </div>
                <p className="muted share-dialog__hint">
                  Dieser Link wird nur einmalig angezeigt und lässt sich später
                  nicht erneut abrufen.
                </p>
                <div className="modal-card__actions">
                  <button className="link" onClick={() => setDialogOpen(false)}>
                    Schließen
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// Zusatzfelder-Sektion (STOAA-113): zeigt ALLE Feld-Definitionen (auch ohne
// Wert → „—") und erlaubt Inline-Bearbeitung bei Schreibrecht. Typkorrekte
// Anzeige (NUMBER deutsch, DATE DD.MM.YYYY, BOOLEAN Ja/Nein) und passende
// Eingabe-Elemente pro Datentyp.
function CustomFieldsPanel({
  fields,
  values,
  canEdit,
  onSave,
  onManageFields,
}: {
  fields: CustomField[];
  values: CustomFieldValue[];
  canEdit: boolean;
  onSave: (values: { field: number; value: string }[]) => Promise<void>;
  onManageFields?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<number, string>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<number, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Aktuellen kanonischen Wert je Feld nachschlagbar machen.
  const valueByField = new Map(values.map((v) => [v.field, v.value]));

  function startEdit() {
    const next: Record<number, string> = {};
    for (const f of fields) {
      next[f.id] = toInputValue(valueByField.get(f.id) ?? "", f.data_type);
    }
    setDraft(next);
    setFieldErrors({});
    setSaveError(null);
    setEditing(true);
  }

  async function save() {
    // Alle Felder validieren + in kanonische Werte konvertieren.
    const out: { field: number; value: string }[] = [];
    const errs: Record<number, string> = {};
    for (const f of fields) {
      const res = toCanonicalValue(draft[f.id] ?? "", f.data_type);
      if (res.error) {
        errs[f.id] = res.error;
      } else {
        out.push({ field: f.id, value: res.value ?? "" });
      }
    }
    if (Object.keys(errs).length > 0) {
      setFieldErrors(errs);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await onSave(out);
      setEditing(false);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  // Ohne Definitionen: dezenter Empty-State mit Verwaltungs-Link (bei Schreibrecht).
  if (fields.length === 0) {
    return (
      <div className="custom-fields">
        <div className="custom-fields__head">
          <h3>Zusatzfelder</h3>
        </div>
        <div className="state-block state-block--subtle">
          <p className="state-block__detail">Keine Zusatzfelder definiert</p>
          {canEdit && onManageFields && (
            <button
              className="link custom-fields__manage"
              onClick={onManageFields}
            >
              Felder verwalten
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="custom-fields">
      <div className="custom-fields__head">
        <h3>Zusatzfelder</h3>
        {canEdit && !editing && (
          <button
            className="link custom-fields__edit"
            onClick={startEdit}
            aria-label="Zusatzfelder bearbeiten"
            title="Zusatzfelder bearbeiten"
          >
            ✎
          </button>
        )}
      </div>

      {editing ? (
        <div className="custom-fields__form">
          {fields.map((f) => (
            <CustomFieldInput
              key={f.id}
              field={f}
              value={draft[f.id] ?? ""}
              error={fieldErrors[f.id]}
              onChange={(v) =>
                setDraft((d) => ({ ...d, [f.id]: v }))
              }
            />
          ))}
          {saveError && (
            <p className="status status--error" role="status">
              {saveError}
            </p>
          )}
          <div className="edit-actions">
            <button onClick={save} disabled={saving}>
              {saving ? "Speichern …" : "Speichern"}
            </button>
            <button
              className="link"
              onClick={() => setEditing(false)}
              disabled={saving}
            >
              Abbrechen
            </button>
          </div>
        </div>
      ) : (
        <dl className="custom-fields__list">
          {fields.map((f) => {
            const raw = valueByField.get(f.id) ?? "";
            const empty = raw === "";
            return (
              <div key={f.id} className="custom-fields__row">
                <dt>{f.name}</dt>
                <dd className={empty ? "muted" : undefined}>
                  {formatCustomFieldValue(raw, f.data_type)}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
    </div>
  );
}

// Einzelnes Edit-Input für ein Zusatzfeld – Element passend zum Datentyp.
function CustomFieldInput({
  field,
  value,
  error,
  onChange,
}: {
  field: CustomField;
  value: string;
  error?: string;
  onChange: (v: string) => void;
}) {
  const inputId = `cf-${field.id}`;
  return (
    <label className="custom-fields__field" htmlFor={inputId}>
      <span className="custom-fields__label">{field.name}</span>
      {field.data_type === "boolean" ? (
        <select
          id={inputId}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">—</option>
          <option value="true">Ja</option>
          <option value="false">Nein</option>
        </select>
      ) : field.data_type === "date" ? (
        <input
          id={inputId}
          type="date"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : field.data_type === "currency" ? (
        <span className="input-with-suffix">
          <input
            id={inputId}
            type="text"
            inputMode="decimal"
            placeholder="z. B. 1234,56"
            value={value}
            onChange={(e) => onChange(e.target.value)}
          />
          <span className="suffix">€</span>
        </span>
      ) : field.data_type === "number" ? (
        <input
          id={inputId}
          type="text"
          inputMode="decimal"
          placeholder="z. B. 1234,56"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input
          id={inputId}
          type="text"
          placeholder="z. B. Vertragsnummer"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {error && (
        <span className="input-error status--error" role="status">
          {error}
        </span>
      )}
    </label>
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

// SHA-256 für die Anzeige kürzen (Anfang…Ende); leere Hashes als "—".
function shortHash(hash: string): string {
  if (!hash) return "—";
  return hash.length > 20 ? `${hash.slice(0, 10)}…${hash.slice(-6)}` : hash;
}

// CSS-Klasse für eine Zeile eines unified-diff (Backend liefert difflib-Output).
function diffLineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) {
    return "compare-diff__line compare-diff__line--meta";
  }
  if (line.startsWith("+")) return "compare-diff__line compare-diff__line--add";
  if (line.startsWith("-")) return "compare-diff__line compare-diff__line--del";
  return "compare-diff__line";
}

// Ein Summary-Badge: grün = unverändert, akzent = geändert.
function CompareBadge({ label, changed }: { label: string; changed: boolean }) {
  return (
    <span
      className={`compare-badge ${
        changed ? "compare-badge--changed" : "compare-badge--same"
      }`}
    >
      {label}: {changed ? "geändert" : "unverändert"}
    </span>
  );
}

// Eine Änderungszeile alt → neu (für Metadaten/Zusatzfelder, Stufe 2).
function ChangeRow({ label, change }: { label: string; change: CompareFieldChange }) {
  return (
    <div className="compare-change">
      <span className="compare-change__label">{label}</span>
      <span className="compare-change__old">{change.old ?? "—"}</span>
      <span className="compare-change__arrow">→</span>
      <span className="compare-change__new">{change.new ?? "—"}</span>
    </div>
  );
}

// Rendert einen ``{added, removed, changed}``-Sektions-Diff (Metadaten bzw.
// Zusatzfelder, Stufe 2 / STOAA-312). ``added`` wird als „— → Wert", ``removed``
// als „Wert → —" dargestellt, ``changed`` mit den echten alt/neu-Werten – alles
// über die bestehende ChangeRow. Leere Sektion → dezenter Hinweis.
function SectionDiff({ diff }: { diff: CompareSectionDiff }) {
  const changed = Object.entries(diff.changed ?? {});
  const added = Object.entries(diff.added ?? {});
  const removed = Object.entries(diff.removed ?? {});

  if (!changed.length && !added.length && !removed.length) {
    return <p className="muted">Keine Änderungen.</p>;
  }

  return (
    <div className="compare-changes">
      {changed.map(([key, change]) => (
        <ChangeRow key={`c-${key}`} label={key} change={change} />
      ))}
      {added.map(([key, value]) => (
        <ChangeRow key={`a-${key}`} label={key} change={{ old: null, new: value }} />
      ))}
      {removed.map(([key, value]) => (
        <ChangeRow key={`r-${key}`} label={key} change={{ old: value, new: null }} />
      ))}
    </div>
  );
}

// Vergleichsansicht (STOAA-290/313): zwei Versionen wählen und OCR-/Datei-Diff
// anzeigen. Metadaten-/Tag-/Feld-Sektionen werden ab Stufe 2 (STOAA-312) befüllt,
// sobald beide Versionen einen Snapshot tragen (``metadata_versioning_supported``);
// sonst greift weiter der Stufe-1-Hinweis „noch nicht verfügbar".
function ComparePanel({
  documentId,
  versions,
  onDownload,
}: {
  documentId: number;
  versions: DocumentVersion[];
  onDownload: (versionNo: number) => void;
}) {
  // ``versions`` ist absteigend sortiert (neueste zuerst).
  const newestNo = versions.length ? versions[0].version_no : null;
  const oldestNo = versions.length ? versions[versions.length - 1].version_no : null;

  // Default: älteste (A) vs. neueste (B).
  const [fromNo, setFromNo] = useState<number | null>(oldestNo);
  const [toNo, setToNo] = useState<number | null>(newestNo);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VersionCompare | null>(null);

  async function onCompare() {
    if (fromNo === null || toNo === null) return;
    if (fromNo === toNo) {
      setError("Bitte zwei unterschiedliche Versionen wählen.");
      setResult(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await compareVersions(documentId, fromNo, toNo);
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  if (versions.length < 2) {
    return (
      <div className="version-info compare-panel">
        <h3>Versionsvergleich</h3>
        <p className="muted">
          Für einen Vergleich werden mindestens zwei Versionen benötigt.
        </p>
      </div>
    );
  }

  return (
    <div className="version-info compare-panel">
      <h3>Versionsvergleich</h3>

      <div className="compare-picker">
        <label className="compare-picker__field">
          <span>Version A</span>
          <select
            value={fromNo ?? ""}
            onChange={(e) => setFromNo(Number(e.target.value))}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.version_no}>
                v{v.version_no}
              </option>
            ))}
          </select>
        </label>
        <label className="compare-picker__field">
          <span>Version B</span>
          <select
            value={toNo ?? ""}
            onChange={(e) => setToNo(Number(e.target.value))}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.version_no}>
                v{v.version_no}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={busy || fromNo === null || toNo === null || fromNo === toNo}
          onClick={onCompare}
        >
          {busy ? "Vergleiche …" : "Vergleichen"}
        </button>
      </div>

      {error && <p className="status status--error">{error}</p>}

      {result && <CompareResultView result={result} onDownload={onDownload} />}
    </div>
  );
}

function CompareResultView({
  result,
  onDownload,
}: {
  result: VersionCompare;
  onDownload: (versionNo: number) => void;
}) {
  const { summary, files } = result;
  // Nur wenn beide Versionen einen Metadaten-Snapshot tragen, liefert das Backend
  // echte Metadaten-/Tag-/Feld-Diffs (Stufe 2). Sonst bleibt es beim Stufe-1-
  // Verhalten: „nicht verfügbar"-Hinweis, keine Sektionen.
  const supported = result.metadata_versioning_supported === true;
  // ``text_diff_html`` VOR dem Rendern sanitizen (Team-Vorgabe DOMPurify) – nie
  // ungesäubertes Backend-HTML in dangerouslySetInnerHTML.
  const safeDiffHtml = sanitizeDiffHtml(result.text_diff_html);
  const hasHtml = !!safeDiffHtml;
  const hasText = hasHtml || !!result.text_diff;

  const tagsAdded = result.tags?.added ?? [];
  const tagsRemoved = result.tags?.removed ?? [];

  return (
    <div className="compare-result">
      <p className="compare-caption">
        Vergleich v{result.from_version} (A) → v{result.to_version} (B)
      </p>

      {/* Summary-Badges */}
      <div className="compare-badges">
        <CompareBadge label="Text" changed={summary.text_changed} />
        <CompareBadge label="Datei" changed={summary.binary_changed} />
        <CompareBadge label="Seiten" changed={summary.pages_changed} />
        {supported && (
          <>
            <CompareBadge label="Metadaten" changed={summary.metadata_changed} />
            <CompareBadge label="Tags" changed={summary.tags_changed} />
            <CompareBadge
              label="Zusatzfelder"
              changed={summary.custom_fields_changed}
            />
          </>
        )}
      </div>
      {!supported && (
        <p className="muted compare-hint">
          Metadaten-, Tag- und Feld-Vergleich pro Version ist noch nicht
          verfügbar (Stufe 2).
        </p>
      )}

      {/* Datei-/Summary-Sektion */}
      <div className="compare-section">
        <h4>Datei</h4>
        <table className="compare-file-table">
          <thead>
            <tr>
              <th />
              <th>Version A (v{result.from_version})</th>
              <th>Version B (v{result.to_version})</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th>SHA-256</th>
              <td className="mono">{shortHash(files.old_sha256)}</td>
              <td className="mono">{shortHash(files.new_sha256)}</td>
            </tr>
            <tr>
              <th>Größe</th>
              <td>{formatBytes(files.old_size)}</td>
              <td>{formatBytes(files.new_size)}</td>
            </tr>
            <tr>
              <th>MIME</th>
              <td>{files.old_mime_type || "—"}</td>
              <td>{files.new_mime_type || "—"}</td>
            </tr>
            <tr>
              <th>Seiten</th>
              <td>{files.old_page_count ?? "—"}</td>
              <td>{files.new_page_count ?? "—"}</td>
            </tr>
          </tbody>
        </table>
        <div className="compare-downloads">
          <button
            type="button"
            className="link"
            onClick={() => onDownload(result.from_version)}
          >
            Version A herunterladen
          </button>
          <button
            type="button"
            className="link"
            onClick={() => onDownload(result.to_version)}
          >
            Version B herunterladen
          </button>
        </div>
      </div>

      {/* OCR-Text-Diff */}
      <div className="compare-section">
        <h4>OCR-Textvergleich</h4>
        {!hasText ? (
          <p className="muted">Kein Textunterschied.</p>
        ) : hasHtml ? (
          <div
            className="compare-diff compare-diff--html"
            // Vom Backend erzeugte HtmlDiff-Tabelle (Stufe 2), DOMPurify-sanitized.
            dangerouslySetInnerHTML={{ __html: safeDiffHtml }}
          />
        ) : (
          <pre className="compare-diff compare-diff--text">
            {result.text_diff.split("\n").map((line, i) => (
              <span key={i} className={diffLineClass(line)}>
                {line + "\n"}
              </span>
            ))}
          </pre>
        )}
      </div>

      {/* Metadaten / Tags / Zusatzfelder – nur bei echter Metadaten-Versionierung
          (Stufe 2, STOAA-312). Ohne beidseitigen Snapshot bleibt es beim
          Stufe-1-Hinweis oben; die Sektionen erscheinen dann gar nicht. */}
      {supported && (
        <>
          <div className="compare-section">
            <h4>Metadaten</h4>
            <SectionDiff diff={result.metadata} />
          </div>

          <div className="compare-section">
            <h4>Tags</h4>
            {tagsAdded.length || tagsRemoved.length ? (
              <div className="compare-tags">
                {tagsAdded.map((t) => (
                  <span
                    key={`a-${t.id}`}
                    className="compare-tag compare-tag--added"
                  >
                    + {t.name}
                  </span>
                ))}
                {tagsRemoved.map((t) => (
                  <span
                    key={`r-${t.id}`}
                    className="compare-tag compare-tag--removed"
                  >
                    − {t.name}
                  </span>
                ))}
              </div>
            ) : (
              <p className="muted">Keine Tag-Änderungen.</p>
            )}
          </div>

          <div className="compare-section">
            <h4>Zusatzfelder</h4>
            <SectionDiff diff={result.custom_fields} />
          </div>
        </>
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
