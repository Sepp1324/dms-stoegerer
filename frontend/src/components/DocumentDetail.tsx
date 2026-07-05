import { useEffect, useRef, useState } from "react";
import {
  addDocumentVersion,
  applySuggestions,
  approveDocument,
  dismissSuggestions,
  getDocument,
  getDocumentIntegrity,
  getDocumentPreview,
  getDocumentQr,
  getDocumentVersionFile,
  rejectDocument,
  retryProcessing,
  submitDocument,
  suggestDocument,
  updateDocument,
  type CustomField,
  type DocumentDetail as Detail,
  type DocumentIntegrity,
  type NamedRef,
} from "../api";
import {
  DETAIL_TABS,
  DetailTabs,
  TabPanel,
  type TabId,
} from "./documentDetail/DetailTabs";
import { DetailPreview } from "./documentDetail/DetailPreview";
import { DetailMeta } from "./documentDetail/DetailMeta";
import { EditForm, type EditFormState } from "./documentDetail/EditForm";
import { AiSuggestionsPanel } from "./documentDetail/AiSuggestionsPanel";
import { VersionsPanel } from "./documentDetail/VersionsPanel";
import { ComparePanel } from "./documentDetail/ComparePanel";
import { ReminderPanel } from "./documentDetail/ReminderPanel";
import { FreigabePanel } from "./documentDetail/FreigabePanel";
import { ShareLinksPanel } from "./documentDetail/ShareLinksPanel";
import { CustomFieldsPanel } from "./documentDetail/CustomFieldsPanel";
import { AuditTrail } from "./documentDetail/AuditPanel";
import { formatIsoDate } from "./documentDetail/format";

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

// Orchestrator der Detailansicht (STOAA-431): hält State/Fetching/Tabs und setzt
// die extrahierten Panels aus ``./documentDetail/`` zusammen. Die einzelnen
// Panels/Helfer liegen als eigene Dateien in diesem Unterordner.
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

  // Aktiver Tab pro Sitzung merkbar (sessionStorage, dokument-spezifisch).
  // Fallback: "overview" (Übersicht) bei fehlendem/ungültigem Wert.
  const [tab, setTab] = useState<TabId>(() => {
    try {
      const stored = sessionStorage.getItem(`dd.tab.${id}`);
      if (stored && DETAIL_TABS.some((t) => t.id === stored)) {
        return stored as TabId;
      }
    } catch {
      /* sessionStorage evtl. nicht verfügbar */
    }
    return "overview";
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(`dd.tab.${id}`, tab);
    } catch {
      /* sessionStorage evtl. nicht verfügbar */
    }
  }, [id, tab]);
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
  const [form, setForm] = useState<EditFormState>({
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
    setTab("overview"); // Edit-Formular erscheint im Übersicht-Kontext.
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
        setRegenNote("KI nicht konfiguriert – ANTHROPIC_API_KEY fehlt im Cluster.");
      } else if (source === "error") {
        setRegenNote("KI-Fehler bei der Generierung – bitte Administrator informieren.");
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

  // KI-Tab nur bei Schreibrecht (Panel ist canEdit-only). Aktiver Tab auf die
  // sichtbaren Tabs klemmen, damit ein gespeicherter „ai"-Tab bei Gästen nicht
  // in eine leere Ansicht führt.
  const visibleTabs = DETAIL_TABS.filter((t) => t.id !== "ai" || canEdit);
  const activeTab: TabId = visibleTabs.some((t) => t.id === tab) ? tab : "overview";

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
          {/* Linke Spalte: große Vorschau, beim Scrollen der rechten Spalte sticky. */}
          <DetailPreview pdfUrl={pdfUrl} pdfError={pdfError} title={doc.title} />

          {/* Rechte Spalte: kompakte Info-/Aktionsspalte mit ARIA-Tabs. */}
          <section className="card detail-panels">
            <DetailTabs tabs={visibleTabs} active={activeTab} onSelect={setTab} />

            {/* Übersicht: Titel, Verarbeitung, Klassifizierung, Metadaten.
                Im Edit-Modus ersetzt das Formular die Anzeige. */}
            <TabPanel id="overview" active={activeTab}>
              {editing ? (
                <EditForm
                  form={form}
                  setForm={setForm}
                  correspondents={correspondents}
                  documentTypes={documentTypes}
                  storagePaths={storagePaths}
                  allTags={allTags}
                  onCreateCorrespondent={onCreateCorrespondent}
                  onCreateDocumentType={onCreateDocumentType}
                  onCreateStoragePath={onCreateStoragePath}
                  onCreateTag={onCreateTag}
                  toggleTag={toggleTag}
                  saving={saving}
                  saveError={saveError}
                  onSave={save}
                  onCancel={() => setEditing(false)}
                />
              ) : (
                <DetailMeta
                  doc={doc}
                  canEdit={canEdit}
                  currentVersion={currentVersion}
                  retryBusy={retryBusy}
                  retryError={retryError}
                  onRetry={onRetry}
                  onDownloadQr={downloadQr}
                />
              )}
            </TabPanel>

            {/* Versionen & Verlauf: Versionsliste/Integrität + Vergleich. */}
            <TabPanel id="versions" active={activeTab}>
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
            </TabPanel>

            {/* KI-Vorschläge (nur bei Schreibrecht). */}
            {canEdit && (
              <TabPanel id="ai" active={activeTab}>
                <AiSuggestionsPanel
                  suggestionRows={suggestionRows}
                  summary={s.summary}
                  applying={applying}
                  regenerating={regenerating}
                  regenNote={regenNote}
                  applyError={applyError}
                  onRegenerate={regenerate}
                  onApply={apply}
                  onDismiss={dismiss}
                />
              </TabPanel>
            )}

            {/* Wiedervorlage. */}
            <TabPanel id="reminder" active={activeTab}>
              <ReminderPanel documentId={id} canEdit={canEdit} />
            </TabPanel>

            {/* Freigabe: Status/Workflow + Freigabelinks. */}
            <TabPanel id="freigabe" active={activeTab}>
              <FreigabePanel
                status={doc.status}
                canEdit={canEdit}
                busy={freigabeBusy}
                error={freigabeError}
                onSubmit={() => runFreigabe(() => submitDocument(id))}
                onApprove={() => runFreigabe(() => approveDocument(id))}
                onReject={(reason) => runFreigabe(() => rejectDocument(id, reason))}
              />
              <ShareLinksPanel documentId={id} canEdit={canEdit} />
            </TabPanel>

            {/* Zusatzfelder. */}
            <TabPanel id="fields" active={activeTab}>
              <CustomFieldsPanel
                fields={customFields}
                values={doc.custom_field_values ?? []}
                canEdit={canEdit}
                onSave={saveCustomFields}
                onManageFields={onManageFields}
              />
            </TabPanel>

            {/* Audit. */}
            <TabPanel id="audit" active={activeTab}>
              <AuditTrail id={id} />
            </TabPanel>
          </section>
        </div>
      )}
    </div>
  );
}
