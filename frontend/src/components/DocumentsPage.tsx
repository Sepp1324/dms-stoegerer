import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  createCorrespondent,
  createDocumentType,
  createStoragePath,
  createTag,
  getCorrespondents,
  getCustomFields,
  getDocuments,
  getDocumentThumbnail,
  getDocumentTypes,
  getMe,
  getStoragePaths,
  getTags,
  getUsers,
  logout,
  setDocumentOwner,
  type CustomField,
  type DocumentItem,
  type Me,
  type NamedRef,
  type ProcessingStateFilter,
  type TagRef,
  type User,
} from "../api";
import { toCanonicalValue } from "../customFields";
import { sanitizeSnippet } from "../sanitize";
import { ProcessingBadge } from "./ProcessingStatus";
import UploadZone from "./UploadZone";
import DocumentDetail from "./DocumentDetail";
import RulesPage from "./RulesPage";
import DuePage from "./DuePage";
import WorkflowsPage from "./WorkflowsPage";
import CustomFieldsAdmin from "./CustomFieldsAdmin";
import MailAccountsAdmin from "./MailAccountsAdmin";

// Von-/Bis-Eingaben eines CURRENCY-Zusatzfeld-Filters (STOAA-113).
type CurrencyRange = { gte: string; lte: string };

// Muss dem Backend entsprechen (DRF PageNumberPagination, config/settings.py:
// REST_FRAMEWORK["PAGE_SIZE"] = 25). Nur für die Anzeige „Seite X von N" nötig;
// die Rand-Buttons werden zusätzlich über next/previous der Antwort abgesichert.
const PAGE_SIZE = 25;

// Der Speicherpfad-Filter nutzt den Backend-Query-Param `storage_path`
// (Kind-Ticket STOAA-49, PR #29 gemergt → DocumentViewSet.get_queryset filtert
// via `storage_path_id`). Der Abschnitt ist damit voll funktionsfähig aktiviert.
const STORAGE_PATH_FILTER_ENABLED = true;

export default function DocumentsPage({ onLogout }: { onLogout: () => void }) {
  const [q, setQ] = useState("");
  const [correspondent, setCorrespondent] = useState<number | "">("");
  const [documentType, setDocumentType] = useState<number | "">("");
  const [tag, setTag] = useState<number | "">("");
  // Speicherpfad-Filter (STOAA-50). Bis der Backend-Query-Param gemergt ist,
  // bleibt der Speicherpfad-Abschnitt in der Sidebar ausgegraut (no-op).
  const [storagePath, setStoragePath] = useState<number | "">("");
  // Verarbeitungsstatus-Filter (STOAA-249): leer = kein Filter, sonst UI-Bucket.
  const [processingState, setProcessingState] = useState<ProcessingStateFilter | "">("");
  // Sortierung; "" = Backend-Standard (FTS-Relevanz bei Suche, sonst Datum neu→alt).
  const [ordering, setOrdering] = useState("");
  // Triage-Ansicht (STOAA-296): zeigt owner-lose Dokumente (?owner=none). Nur für
  // Admins sichtbar/aktivierbar; lädt die Nutzerliste erst bei Bedarf.
  const [triage, setTriage] = useState(false);
  const [users, setUsers] = useState<User[]>([]);

  const [correspondents, setCorrespondents] = useState<NamedRef[]>([]);
  const [documentTypes, setDocumentTypes] = useState<NamedRef[]>([]);
  const [tags, setTags] = useState<TagRef[]>([]);
  const [storagePaths, setStoragePaths] = useState<NamedRef[]>([]);
  // Zusatzfeld-Definitionen (STOAA-113) für Anzeige (DocumentDetail) + Filter.
  const [customFields, setCustomFields] = useState<CustomField[]>([]);
  // CURRENCY-Filter: pro Feld-ID Von-/Bis-Eingaben (roh, deutsches Format).
  const [currencyFilters, setCurrencyFilters] = useState<
    Record<number, CurrencyRange>
  >({});

  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [count, setCount] = useState(0);
  // Aktuelle Seite (1-basiert, wie das DRF-`page`-Query). Jede Filter-/Such-
  // änderung setzt zurück auf 1 (siehe onSearchChange & Co.).
  const [page, setPage] = useState(1);
  // Ob es eine nächste/vorige Seite gibt – direkt aus der API-Antwort, damit die
  // Rand-Buttons auch ohne PAGE_SIZE-Annahme korrekt deaktiviert werden.
  const [hasNext, setHasNext] = useState(false);
  const [hasPrev, setHasPrev] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [me, setMe] = useState<Me | null>(null);
  // Wird nach jedem Upload erhöht → löst ein Neuladen der Liste aus.
  const [reloadKey, setReloadKey] = useState(0);
  // Aktuell geöffnetes Dokument (Detailansicht) oder null (Liste).
  const [selectedId, setSelectedId] = useState<number | null>(null);
  // Aktive Hauptansicht (persistente linke Navigation).
  const [view, setView] = useState<"docs" | "rules" | "workflows" | "fields" | "mail" | "faellig">("docs");
  // Sidebar auf schmalen Screens ein-/ausklappbar.
  const [navOpen, setNavOpen] = useState(false);

  // Zusatzfeld-Definitionen laden (auch nach Admin-Änderungen erneut aufrufbar).
  function loadCustomFields() {
    getCustomFields()
      .then(setCustomFields)
      .catch(() => {
        /* Zusatzfelder optional – Fehler hier nicht blockierend */
      });
  }

  // Profil + Filter-Stammdaten einmalig laden.
  useEffect(() => {
    getMe().then(setMe).catch(() => {});
    Promise.all([
      getCorrespondents(),
      getDocumentTypes(),
      getTags(),
      getStoragePaths(),
    ])
      .then(([c, d, t, s]) => {
        setCorrespondents(c);
        setDocumentTypes(d);
        setTags(t);
        setStoragePaths(s);
      })
      .catch(() => {
        /* Stammdaten sind optional – Fehler hier nicht blockierend */
      });
    loadCustomFields();
  }, []);

  // Nutzerliste für das „Owner setzen"-Dropdown der Triage-Ansicht. Der Endpunkt
  // ist admin-only (403 für Normalnutzer), daher erst laden, wenn feststeht, dass
  // der aktuelle Nutzer Admin ist. Muster wie MailAccountsAdmin (STOAA-215/233).
  useEffect(() => {
    if (!me?.is_dms_admin) return;
    getUsers()
      .then(setUsers)
      .catch(() => {
        /* Nutzerliste optional – Fehler hier nicht blockierend */
      });
  }, [me?.is_dms_admin]);

  // Stammdaten inline anlegen: erzeugen, in die lokale Liste einsortieren, Item zurückgeben.
  const byName = (a: NamedRef, b: NamedRef) => a.name.localeCompare(b.name);
  async function addCorrespondent(name: string) {
    const item = await createCorrespondent(name);
    setCorrespondents((prev) => [...prev, item].sort(byName));
    return item;
  }
  async function addDocumentType(name: string) {
    const item = await createDocumentType(name);
    setDocumentTypes((prev) => [...prev, item].sort(byName));
    return item;
  }
  async function addStoragePath(name: string) {
    const item = await createStoragePath(name);
    setStoragePaths((prev) => [...prev, item].sort(byName));
    return item;
  }
  async function addTag(name: string) {
    const item = await createTag(name);
    setTags((prev) => [...prev, item].sort(byName));
    return item;
  }

  // Suchfeld entprellen, damit nicht jeder Tastendruck eine Anfrage auslöst.
  const [debouncedQ, setDebouncedQ] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(id);
  }, [q]);

  // CURRENCY-Filtereingaben ebenfalls entprellen (Zahlen sind kurz → 400 ms).
  const [debouncedCurrency, setDebouncedCurrency] = useState<
    Record<number, CurrencyRange>
  >({});
  useEffect(() => {
    const id = setTimeout(() => setDebouncedCurrency(currencyFilters), 400);
    return () => clearTimeout(id);
  }, [currencyFilters]);

  // Nur CURRENCY-Felder sind in P1 filterbar (Spec §4.1).
  const currencyFields = useMemo(
    () => customFields.filter((f) => f.data_type === "currency"),
    [customFields],
  );

  // Entprellte Eingaben → gültige, kanonische Query-Params
  // (custom_field_{id}_gte / _lte). Ungültige/leere Grenzen werden ausgelassen;
  // das Backend ignoriert unbekannte Grenzen ohnehin (kein 500).
  const customFilters = useMemo(() => {
    const out: Record<string, string> = {};
    for (const f of currencyFields) {
      const range = debouncedCurrency[f.id];
      if (!range) continue;
      if (range.gte?.trim()) {
        const g = toCanonicalValue(range.gte, "currency");
        if (g.value) out[`custom_field_${f.id}_gte`] = g.value;
      }
      if (range.lte?.trim()) {
        const l = toCanonicalValue(range.lte, "currency");
        if (l.value) out[`custom_field_${f.id}_lte`] = l.value;
      }
    }
    return out;
  }, [currencyFields, debouncedCurrency]);
  const customFilterKey = JSON.stringify(customFilters);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    getDocuments({
      q: debouncedQ,
      correspondent,
      document_type: documentType,
      tag,
      storage_path: storagePath,
      processing_state: processingState,
      // Triage nur für Admins anfordern; das Backend ignoriert den Param für
      // Normalnutzer ohnehin, aber so bleibt die FE-Absicht eindeutig.
      owner: triage && me?.is_dms_admin ? "none" : "",
      ordering,
      page,
      customFilters,
    })
      .then((res) => {
        if (!active) return;
        setDocs(res.results);
        setCount(res.count);
        setHasNext(res.next !== null);
        setHasPrev(res.previous !== null);
      })
      .catch((err) => active && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
    // customFilterKey serialisiert customFilters für einen stabilen Dep-Vergleich.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ, correspondent, documentType, tag, storagePath, processingState, triage, me?.is_dms_admin, ordering, page, reloadKey, customFilterKey]);

  // Sichtbarkeit von „Zurücksetzen" & Empty-State-Text: alle roh getippten Filter.
  const hasCurrencyInput = useMemo(
    () =>
      Object.values(currencyFilters).some((r) => r.gte?.trim() || r.lte?.trim()),
    [currencyFilters],
  );
  const hasFilters = useMemo(
    () =>
      !!(debouncedQ || correspondent || documentType || tag || storagePath || processingState) ||
      hasCurrencyInput,
    [debouncedQ, correspondent, documentType, tag, storagePath, processingState, hasCurrencyInput],
  );

  // Jede Filter-/Suchänderung springt zurück auf Seite 1 – sonst zeigt eine
  // hohe Seitenzahl nach dem Einschränken u. U. „keine Treffer".
  function onSearchChange(v: string) {
    setQ(v);
    setPage(1);
  }
  function onCorrespondentChange(v: number | "") {
    setCorrespondent(v);
    setPage(1);
  }
  function onDocumentTypeChange(v: number | "") {
    setDocumentType(v);
    setPage(1);
  }
  function onTagChange(v: number | "") {
    setTag(v);
    setPage(1);
  }
  function onStoragePathChange(v: number | "") {
    setStoragePath(v);
    setPage(1);
  }
  function onProcessingStateChange(v: ProcessingStateFilter | "") {
    setProcessingState(v);
    setPage(1);
  }
  function onOrderingChange(v: string) {
    setOrdering(v);
    setPage(1);
  }
  // Triage-Ansicht umschalten (nur Admins). Zurück auf Seite 1, damit nach dem
  // Moduswechsel keine leere hohe Seitenzahl gezeigt wird.
  function onToggleTriage() {
    setTriage((t) => !t);
    setPage(1);
  }
  // Eine Von-/Bis-Grenze eines CURRENCY-Feldes setzen (setzt auf Seite 1 zurück).
  function onCurrencyChange(fieldId: number, bound: keyof CurrencyRange, v: string) {
    setCurrencyFilters((prev) => {
      const cur = prev[fieldId] ?? { gte: "", lte: "" };
      return { ...prev, [fieldId]: { ...cur, [bound]: v } };
    });
    setPage(1);
  }

  function resetFilters() {
    setQ("");
    setCorrespondent("");
    setDocumentType("");
    setTag("");
    setStoragePath("");
    setProcessingState("");
    setOrdering("");
    setCurrencyFilters({});
    setPage(1);
  }

  function handleLogout() {
    logout();
    onLogout();
  }

  if (selectedId !== null) {
    return (
      <DocumentDetail
        id={selectedId}
        onBack={() => {
          setSelectedId(null);
          setReloadKey((k) => k + 1); // ggf. geänderte Metadaten in der Liste zeigen
        }}
        correspondents={correspondents}
        documentTypes={documentTypes}
        storagePaths={storagePaths}
        allTags={tags}
        customFields={customFields}
        canEdit={!!me?.can_write}
        onCreateCorrespondent={addCorrespondent}
        onCreateDocumentType={addDocumentType}
        onCreateStoragePath={addStoragePath}
        onCreateTag={addTag}
        onManageFields={
          me?.is_dms_admin
            ? () => {
                setSelectedId(null);
                setView("fields");
              }
            : undefined
        }
      />
    );
  }

  const navigate = (v: "docs" | "rules" | "workflows" | "fields" | "mail" | "faellig") => {
    setView(v);
    setNavOpen(false); // Overlay auf Mobil nach Auswahl schließen
  };

  return (
    <div className="layout">
      <Sidebar
        view={view}
        onNavigate={navigate}
        username={me?.username}
        onLogout={handleLogout}
        isAdmin={!!me?.is_dms_admin}
        open={navOpen}
        onClose={() => setNavOpen(false)}
        correspondents={correspondents}
        tags={tags}
        documentTypes={documentTypes}
        storagePaths={storagePaths}
        correspondent={correspondent}
        tag={tag}
        documentType={documentType}
        storagePath={storagePath}
        processingState={processingState}
        onCorrespondentChange={onCorrespondentChange}
        onTagChange={onTagChange}
        onDocumentTypeChange={onDocumentTypeChange}
        onStoragePathChange={onStoragePathChange}
        onProcessingStateChange={onProcessingStateChange}
        storagePathEnabled={STORAGE_PATH_FILTER_ENABLED}
        currencyFields={currencyFields}
        currencyFilters={currencyFilters}
        onCurrencyChange={onCurrencyChange}
      />

      <div className="content">
        <header className="content-topbar">
          <button
            className="nav-toggle"
            aria-label="Navigation öffnen"
            onClick={() => setNavOpen(true)}
          >
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <path fill="currentColor" d="M3 6h18v2H3zm0 5h18v2H3zm0 5h18v2H3z" />
            </svg>
          </button>
          <h1 className="content-title">
            {view === "rules"
              ? "Regeln"
              : view === "workflows"
                ? "Workflows"
                : view === "fields"
                  ? "Zusatzfelder"
                  : view === "mail"
                    ? "Mailkonten"
                    : view === "faellig"
                      ? "Wiedervorlage"
                      : "Dokumente"}
          </h1>
          {view === "docs" && (
            <input
              className="search topbar-search"
              placeholder="Volltextsuche (Titel & Inhalt) …"
              value={q}
              onChange={(e) => onSearchChange(e.target.value)}
            />
          )}
        </header>

        <div className="content-body">
          {view === "rules" ? (
            <RulesPage canEdit={!!me?.can_write} />
          ) : view === "workflows" ? (
            <WorkflowsPage canEdit={!!me?.can_write} />
          ) : view === "fields" ? (
            <CustomFieldsAdmin
              canEdit={!!me?.can_write}
              onChanged={loadCustomFields}
            />
          ) : view === "mail" ? (
            <MailAccountsAdmin canEdit={!!me?.can_write} />
          ) : view === "faellig" ? (
            <DuePage onOpenDocument={(docId) => setSelectedId(docId)} />
          ) : (
            <>
              {me?.can_write && (
                <UploadZone
                  onUploaded={() => {
                    // Neue Dokumente stehen (ordering "-added_at") auf Seite 1.
                    setPage(1);
                    setReloadKey((k) => k + 1);
                  }}
                />
              )}

              {/* Stammdaten-Filter leben jetzt in der Sidebar (STOAA-50); die
                  Topleiste beschränkt sich auf Sortierung + Zurücksetzen. */}
              <section className="filters card">
                <div className="filter-row">
                  {/* Triage-Umschalter nur für Admins: zeigt owner-lose Dokumente
                      (STOAA-296). Für Normalnutzer nicht sichtbar/aktivierbar. */}
                  {me?.is_dms_admin && (
                    <button
                      type="button"
                      className={`triage-toggle${triage ? " triage-toggle--active" : ""}`}
                      onClick={onToggleTriage}
                      aria-pressed={triage}
                    >
                      {triage ? "Alle Dokumente" : "Nicht zugeordnet (Triage)"}
                    </button>
                  )}
                  <label className="filter">
                    <span>Sortierung</span>
                    <select value={ordering} onChange={(e) => onOrderingChange(e.target.value)}>
                      <option value="">Standard</option>
                      <option value="-added_at">Datum (neu → alt)</option>
                      <option value="added_at">Datum (alt → neu)</option>
                      <option value="title">Titel (A–Z)</option>
                      <option value="-title">Titel (Z–A)</option>
                    </select>
                  </label>
                  {hasFilters && (
                    <button className="link" onClick={resetFilters}>
                      Zurücksetzen
                    </button>
                  )}
                </div>
              </section>

              <section>
                {loading ? (
                  <SkeletonGrid />
                ) : error ? (
                  <StateBlock
                    title="Dokumente konnten nicht geladen werden"
                    detail={error}
                    tone="error"
                    action={
                      <button onClick={() => setReloadKey((k) => k + 1)}>
                        Erneut versuchen
                      </button>
                    }
                  />
                ) : docs.length === 0 ? (
                  <StateBlock
                    title={
                      triage
                        ? "Keine Dokumente ohne Eigentümer"
                        : hasFilters
                          ? "Keine Treffer für die aktuellen Filter"
                          : "Noch keine Dokumente"
                    }
                    detail={
                      triage
                        ? "Aktuell ist alles zugeordnet – hier landen owner-lose Mail-/Consume-Importe."
                        : hasFilters
                          ? "Passe die Suche oder Filter an."
                          : "Lade ein Dokument hoch, um zu beginnen."
                    }
                    action={
                      !triage && hasFilters ? (
                        <button className="link" onClick={resetFilters}>
                          Filter zurücksetzen
                        </button>
                      ) : undefined
                    }
                  />
                ) : (
                  <>
                    <p className="muted result-count">
                      {count} {count === 1 ? "Dokument" : "Dokumente"}
                    </p>
                    <div className="doc-grid">
                      {docs.map((d) =>
                        triage ? (
                          <TriageCard
                            key={d.id}
                            doc={d}
                            users={users}
                            onOpen={() => setSelectedId(d.id)}
                            onAssigned={() => setReloadKey((k) => k + 1)}
                          />
                        ) : (
                          <DocumentCard
                            key={d.id}
                            doc={d}
                            onOpen={() => setSelectedId(d.id)}
                          />
                        ),
                      )}
                    </div>
                    <Pagination
                      page={page}
                      totalPages={Math.max(1, Math.ceil(count / PAGE_SIZE))}
                      hasPrev={hasPrev}
                      hasNext={hasNext}
                      onPrev={() => setPage((p) => Math.max(1, p - 1))}
                      onNext={() => setPage((p) => p + 1)}
                    />
                  </>
                )}
              </section>
            </>
          )}
        </div>
      </div>

      {navOpen && (
        <div className="nav-backdrop" onClick={() => setNavOpen(false)} />
      )}
    </div>
  );
}

// Persistente linke Navigation (paperless-like). Auf schmalen Screens als
// Overlay über `open` gesteuert; Aktiv-Zustand über `view`. Unter der Haupt-
// navigation zeigen ausklappbare Stammdaten-Abschnitte (Korrespondenten, Tags,
// Dokumenttypen, Speicherpfade) klickbare Filterlisten (STOAA-50).
// STOAA-410: Sidebar einklappbar (localStorage), Filter eigenständig scrollbar.
const SIDEBAR_COLLAPSED_KEY = "dms:sidebar:collapsed";

function Sidebar({
  view,
  onNavigate,
  username,
  onLogout,
  isAdmin,
  open,
  onClose,
  correspondents,
  tags,
  documentTypes,
  storagePaths,
  correspondent,
  tag,
  documentType,
  storagePath,
  processingState,
  onCorrespondentChange,
  onTagChange,
  onDocumentTypeChange,
  onStoragePathChange,
  onProcessingStateChange,
  storagePathEnabled,
  currencyFields,
  currencyFilters,
  onCurrencyChange,
}: {
  view: "docs" | "rules" | "workflows" | "fields" | "mail" | "faellig";
  onNavigate: (v: "docs" | "rules" | "workflows" | "fields" | "mail" | "faellig") => void;
  username?: string;
  onLogout: () => void;
  isAdmin: boolean;
  open: boolean;
  onClose: () => void;
  correspondents: NamedRef[];
  tags: TagRef[];
  documentTypes: NamedRef[];
  storagePaths: NamedRef[];
  correspondent: number | "";
  tag: number | "";
  documentType: number | "";
  storagePath: number | "";
  processingState: ProcessingStateFilter | "";
  onCorrespondentChange: (v: number | "") => void;
  onTagChange: (v: number | "") => void;
  onDocumentTypeChange: (v: number | "") => void;
  onStoragePathChange: (v: number | "") => void;
  onProcessingStateChange: (v: ProcessingStateFilter | "") => void;
  storagePathEnabled: boolean;
  currencyFields: CustomField[];
  currencyFilters: Record<number, CurrencyRange>;
  onCurrencyChange: (
    fieldId: number,
    bound: keyof CurrencyRange,
    v: string,
  ) => void;
}) {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1",
  );

  function toggleCollapse() {
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? "1" : "0");
      return next;
    });
  }

  // Nach einer Filterauswahl auf Mobil das Overlay schließen (Desktop no-op).
  const pick = (fn: (v: number | "") => void) => (v: number | "") => {
    fn(v);
    onClose();
  };

  const cls = ["sidebar", open && "sidebar--open", collapsed && "sidebar--collapsed"]
    .filter(Boolean)
    .join(" ");

  return (
    <aside className={cls}>
      <div className="sidebar__brand">
        <span className="sidebar__logo">DMS</span>
        {/* Schließen-Button nur auf Mobil */}
        <button
          className="nav-toggle sidebar__close"
          aria-label="Navigation schließen"
          onClick={onClose}
        >
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path
              fill="currentColor"
              d="M6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12 19 6.4 17.6 5 12 10.6z"
            />
          </svg>
        </button>
        {/* Collapse-Toggle auf Desktop */}
        <button
          className="sidebar__collapse-btn"
          aria-label={collapsed ? "Sidebar aufklappen" : "Sidebar einklappen"}
          onClick={toggleCollapse}
          title={collapsed ? "Aufklappen" : "Einklappen"}
        >
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            {collapsed
              ? <path fill="currentColor" d="M8 5l8 7-8 7z" />
              : <path fill="currentColor" d="M16 5l-8 7 8 7z" />}
          </svg>
        </button>
      </div>

      <nav className="nav">
        <NavItem
          active={view === "docs"}
          onClick={() => onNavigate("docs")}
          label="Dokumente"
          icon="M6 2h7l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2m7 1.5V8h4.5z"
        />
        <NavItem
          active={view === "faellig"}
          onClick={() => onNavigate("faellig")}
          label="Wiedervorlage"
          icon="M12 2a6 6 0 0 0-6 6c0 3.5-1 5-2 6v1h16v-1c-1-1-2-2.5-2-6a6 6 0 0 0-6-6m0 20a2 2 0 0 0 2-2h-4a2 2 0 0 0 2 2z"
        />
        <NavItem
          active={view === "rules"}
          onClick={() => onNavigate("rules")}
          label="Regeln"
          icon="M3 5h18v2H3zm0 6h12v2H3zm0 6h18v2H3z"
        />
        <NavItem
          active={view === "workflows"}
          onClick={() => onNavigate("workflows")}
          label="Workflows"
          icon="M4 4h6v6H4zm10 0h6v6h-6zM4 14h6v6H4zm13 0v3h3v2h-3v3h-2v-3h-3v-2h3v-3z"
        />
        {isAdmin && (
          <NavItem
            active={view === "fields"}
            onClick={() => onNavigate("fields")}
            label="Zusatzfelder"
            icon="M4 4h16v4H4zm0 6h16v4H4zm0 6h10v4H4z"
          />
        )}
        {isAdmin && (
          <NavItem
            active={view === "mail"}
            onClick={() => onNavigate("mail")}
            label="Mailkonten"
            icon="M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2m0 2v.5l8 5 8-5V6H4m0 2.8V18h16V8.8l-8 5z"
          />
        )}

      </nav>

      {/* Stammdaten-Filter: eigenständig scrollbarer Bereich, nur in Docs-Ansicht */}
      {view === "docs" && (
        <div className="sidebar__filters">
          <ProcessingFilterSection
            active={processingState}
            onSelect={(v) => {
              onProcessingStateChange(v);
              onClose();
            }}
          />
          <FilterSection
            title="Korrespondenten"
            items={correspondents}
            activeId={correspondent}
            onSelect={pick(onCorrespondentChange)}
            searchable
          />
          <FilterSection
            title="Tags"
            items={tags}
            activeId={tag}
            onSelect={pick(onTagChange)}
            colored
            searchable
          />
          <FilterSection
            title="Dokumenttypen"
            items={documentTypes}
            activeId={documentType}
            onSelect={pick(onDocumentTypeChange)}
          />
          <FilterSection
            title="Speicherpfade"
            items={storagePaths}
            activeId={storagePath}
            onSelect={pick(onStoragePathChange)}
            disabled={!storagePathEnabled}
            note={storagePathEnabled ? undefined : "Backend folgt"}
          />
          <CurrencyFilterSection
            fields={currencyFields}
            filters={currencyFilters}
            onChange={onCurrencyChange}
          />
        </div>
      )}

      <div className="sidebar__footer">
        {username && <span className="muted sidebar__user">{username}</span>}
        <button className="link" onClick={onLogout}>
          Abmelden
        </button>
      </div>
    </aside>
  );
}

const FILTER_TOP_N = 5;

// Ausklappbarer Stammdaten-Abschnitt der Sidebar: Titel + Anzahl, darunter eine
// Liste klickbarer Filter. Standardmäßig eingeklappt; bei langen Listen Top-N + mehr
// anzeigen + optionales Suchfeld. Klick auf aktiven Eintrag hebt Filter wieder auf.
function FilterSection({
  title,
  items,
  activeId,
  onSelect,
  colored,
  disabled,
  note,
  searchable,
}: {
  title: string;
  items: (NamedRef & { color?: string })[];
  activeId: number | "";
  onSelect: (v: number | "") => void;
  colored?: boolean;
  disabled?: boolean;
  note?: string;
  searchable?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [q, setQ] = useState("");
  if (items.length === 0) return null;

  // Bei aktivem Filter immer aufklappen
  const hasActive = activeId !== "" && items.some((i) => i.id === activeId);
  const isExpanded = expanded || hasActive;

  const filtered = q
    ? items.filter((i) => i.name.toLowerCase().includes(q.toLowerCase()))
    : items;
  const visible = showAll || q ? filtered : filtered.slice(0, FILTER_TOP_N);
  const hiddenCount = filtered.length - visible.length;

  return (
    <div className={`nav-section${disabled ? " nav-section--disabled" : ""}`}>
      <button
        className="nav-section__head"
        onClick={() => { setExpanded((e) => !e); if (isExpanded) { setQ(""); setShowAll(false); } }}
        aria-expanded={isExpanded}
      >
        <svg
          className={`nav-section__chevron${isExpanded ? " nav-section__chevron--open" : ""}`}
          viewBox="0 0 24 24"
          width="14"
          height="14"
          aria-hidden="true"
        >
          <path fill="currentColor" d="M8 5l8 7-8 7z" />
        </svg>
        <span className="nav-section__title">{title}</span>
        {note ? (
          <span className="nav-section__note">{note}</span>
        ) : (
          <span className="nav-section__count">{activeId !== "" && hasActive ? "✓" : items.length}</span>
        )}
      </button>
      {isExpanded && (
        <>
          {searchable && items.length > FILTER_TOP_N && (
            <input
              className="nav-section__search"
              placeholder="Filtern …"
              value={q}
              onChange={(e) => { setQ(e.target.value); setShowAll(false); }}
              onClick={(e) => e.stopPropagation()}
            />
          )}
          <ul className="nav-section__list">
            {visible.map((it) => {
              const active = activeId === it.id;
              return (
                <li key={it.id}>
                  <button
                    className={`nav-filter${active ? " nav-filter--active" : ""}`}
                    onClick={() => onSelect(active ? "" : it.id)}
                    aria-current={active ? "true" : undefined}
                    disabled={disabled}
                    title={it.name}
                  >
                    {colored && (
                      <span
                        className="nav-filter__dot"
                        style={{ background: it.color ?? "var(--muted)" }}
                      />
                    )}
                    <span className="nav-filter__label">{it.name}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          {hiddenCount > 0 && (
            <button className="nav-section__more" onClick={() => setShowAll(true)}>
              {hiddenCount} weitere …
            </button>
          )}
        </>
      )}
    </div>
  );
}

// Verarbeitungsstatus-Filter (STOAA-249): feste UI-Buckets auf den
// ``processing_state`` der aktuellen Version. Klick auf den aktiven Bucket hebt
// den Filter wieder auf. Analog zu FilterSection, aber string-basiert.
const PROCESSING_FILTERS: { value: ProcessingStateFilter; label: string }[] = [
  { value: "failed", label: "Fehlgeschlagen" },
  { value: "processing", label: "In Verarbeitung" },
  { value: "retry_pending", label: "Wartet auf Retry" },
  { value: "ready", label: "Bereit" },
];

function ProcessingFilterSection({
  active,
  onSelect,
}: {
  active: ProcessingStateFilter | "";
  onSelect: (v: ProcessingStateFilter | "") => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="nav-section">
      <button
        className="nav-section__head"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        <svg
          className={`nav-section__chevron${expanded ? " nav-section__chevron--open" : ""}`}
          viewBox="0 0 24 24"
          width="14"
          height="14"
          aria-hidden="true"
        >
          <path fill="currentColor" d="M8 5l8 7-8 7z" />
        </svg>
        <span className="nav-section__title">Verarbeitung</span>
        <span className="nav-section__count">{PROCESSING_FILTERS.length}</span>
      </button>
      {expanded && (
        <ul className="nav-section__list">
          {PROCESSING_FILTERS.map((f) => {
            const isActive = active === f.value;
            return (
              <li key={f.value}>
                <button
                  className={`nav-filter${isActive ? " nav-filter--active" : ""}`}
                  onClick={() => onSelect(isActive ? "" : f.value)}
                  aria-current={isActive ? "true" : undefined}
                  title={f.label}
                >
                  <span className="nav-filter__label">{f.label}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// CURRENCY-Zusatzfeld-Filter (STOAA-113): pro Währungsfeld ein Von-/Bis-Paar.
// Wird ausgeblendet, wenn keine CURRENCY-Felder definiert sind.
function CurrencyFilterSection({
  fields,
  filters,
  onChange,
}: {
  fields: CustomField[];
  filters: Record<number, CurrencyRange>;
  onChange: (fieldId: number, bound: keyof CurrencyRange, v: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (fields.length === 0) return null;

  return (
    <div className="nav-section">
      <button
        className="nav-section__head"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        <svg
          className={`nav-section__chevron${expanded ? " nav-section__chevron--open" : ""}`}
          viewBox="0 0 24 24"
          width="14"
          height="14"
          aria-hidden="true"
        >
          <path fill="currentColor" d="M8 5l8 7-8 7z" />
        </svg>
        <span className="nav-section__title">Beträge</span>
        <span className="nav-section__count">{fields.length}</span>
      </button>
      {expanded && (
        <div className="nav-section__list currency-filters">
          {fields.map((f) => {
            const range = filters[f.id] ?? { gte: "", lte: "" };
            return (
              <div key={f.id} className="currency-filter">
                <span className="currency-filter__name">{f.name}</span>
                <div className="currency-filter__row">
                  <label className="currency-filter__field">
                    <span className="currency-filter__label">Von</span>
                    <input
                      type="text"
                      inputMode="decimal"
                      placeholder="0"
                      aria-label={`${f.name} von`}
                      value={range.gte}
                      onChange={(e) => onChange(f.id, "gte", e.target.value)}
                    />
                  </label>
                  <label className="currency-filter__field">
                    <span className="currency-filter__label">Bis</span>
                    <input
                      type="text"
                      inputMode="decimal"
                      placeholder="∞"
                      aria-label={`${f.name} bis`}
                      value={range.lte}
                      onChange={(e) => onChange(f.id, "lte", e.target.value)}
                    />
                  </label>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function NavItem({
  active,
  onClick,
  label,
  icon,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  icon: string;
}) {
  return (
    <button
      className={`nav-item${active ? " nav-item--active" : ""}`}
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      title={label}
    >
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" style={{ flexShrink: 0 }}>
        <path fill="currentColor" d={icon} />
      </svg>
      <span className="nav-item__label">{label}</span>
    </button>
  );
}

// Skeleton-Karten während des Ladens (gleiches Raster wie die echte Liste).
function SkeletonGrid() {
  return (
    <div className="doc-grid" aria-hidden="true">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="doc-card doc-card--skeleton">
          <div className="doc-card__preview skeleton" />
          <div className="doc-card__body">
            <div className="skeleton skeleton-line" />
            <div className="skeleton skeleton-line skeleton-line--short" />
          </div>
        </div>
      ))}
    </div>
  );
}

// Einheitlicher Leer-/Fehler-Zustand (gleiche Gestaltung überall).
function StateBlock({
  title,
  detail,
  action,
  tone,
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
  tone?: "error";
}) {
  return (
    <div className={`state-block${tone === "error" ? " state-block--error" : ""}`}>
      <p className="state-block__title">{title}</p>
      {detail && <p className="state-block__detail">{detail}</p>}
      {action && <div className="state-block__action">{action}</div>}
    </div>
  );
}

// Seiten-Navigation für die Dokumentliste. Wird nur gerendert, wenn es mehr als
// eine Seite gibt. Die Rand-Buttons sind über die tatsächlichen next/previous der
// API deaktiviert; die Beschriftung „Seite X von N" nutzt count + PAGE_SIZE.
function Pagination({
  page,
  totalPages,
  hasPrev,
  hasNext,
  onPrev,
  onNext,
}: {
  page: number;
  totalPages: number;
  hasPrev: boolean;
  hasNext: boolean;
  onPrev: () => void;
  onNext: () => void;
}) {
  if (totalPages <= 1) return null;
  return (
    <nav className="pagination" aria-label="Seiten-Navigation">
      <button className="pagination__btn" onClick={onPrev} disabled={!hasPrev}>
        ← Zurück
      </button>
      <span className="pagination__status muted" aria-live="polite">
        Seite {page} von {totalPages}
      </span>
      <button className="pagination__btn" onClick={onNext} disabled={!hasNext}>
        Weiter →
      </button>
    </nav>
  );
}

function DocumentCard({ doc, onOpen }: { doc: DocumentItem; onOpen: () => void }) {
  const [thumb, setThumb] = useState<string | null>(null);

  useEffect(() => {
    let url: string | null = null;
    let active = true;
    getDocumentThumbnail(doc.id)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setThumb(url);
      })
      .catch(() => {
        /* kein Thumbnail (z. B. OCR noch nicht fertig) → Icon-Fallback */
      });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [doc.id]);

  return (
    <button className="doc-card" onClick={onOpen} title={doc.title}>
      <div className="doc-card__preview">
        {thumb ? (
          <img className="doc-card__thumb" src={thumb} alt="" />
        ) : (
          <svg viewBox="0 0 24 24" width="38" height="38" aria-hidden="true">
            <path
              fill="currentColor"
              d="M6 2h7l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2m7 1.5V8h4.5z"
            />
          </svg>
        )}
        {doc.page_count != null && (
          <span className="doc-card__pages">
            {doc.page_count} {doc.page_count === 1 ? "Seite" : "Seiten"}
          </span>
        )}
      </div>
      <div className="doc-card__body">
        <h3 className="doc-card__title">{doc.title}</h3>
        <p className="doc-card__meta">
          {doc.correspondent_name ?? "Unbekannt"}
          {doc.document_type_name ? ` · ${doc.document_type_name}` : ""}
        </p>
        {/* Suchergebnis-Snippet (STOAA-368/370): nur bei aktiver Suche gefüllt.
            Backend liefert bereits sicheres HTML (nur <mark>); sanitizeSnippet
            ist Defense-in-Depth vor dangerouslySetInnerHTML. */}
        {doc.snippet && (
          <p
            className="doc-card__snippet"
            dangerouslySetInnerHTML={{ __html: sanitizeSnippet(doc.snippet) }}
          />
        )}
        {doc.tags.length > 0 && (
          <div className="doc-card__tags">
            {doc.tags.map((t) => (
              <span key={t.id} className="tag" style={{ borderColor: t.color, color: t.color }}>
                {t.name}
              </span>
            ))}
          </div>
        )}
        <p className="doc-card__footer">
          <span className="doc-card__date">
            {new Date(doc.added_at).toLocaleDateString("de-DE")}
          </span>
          <ProcessingBadge state={doc.processing_state} />
        </p>
      </div>
    </button>
  );
}

// Triage-Karte (STOAA-296): owner-loses Dokument mit „Owner setzen"-Aktion.
// Anders als DocumentCard ist die Karte ein <div> (verschachtelte Buttons wären
// ungültig): der Titel öffnet das Detail, darunter weist ein Dropdown + Button
// über POST set-owner (admin-only) einen Eigentümer zu. Nach Erfolg lädt die
// Liste neu (onAssigned) – das zugewiesene Dokument fällt aus ?owner=none heraus.
function TriageCard({
  doc,
  users,
  onOpen,
  onAssigned,
}: {
  doc: DocumentItem;
  users: User[];
  onOpen: () => void;
  onAssigned: () => void;
}) {
  const [userId, setUserId] = useState<number | "">("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function assign() {
    if (userId === "") return;
    setSaving(true);
    setError(null);
    try {
      await setDocumentOwner(doc.id, userId);
      onAssigned(); // Liste refreshen – Karte verschwindet aus der Triage-Liste
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSaving(false); // bei Erfolg bleibt saving aktiv bis zum Neuladen
    }
  }

  return (
    <div className="doc-card doc-card--triage">
      <button className="triage-card__open" onClick={onOpen} title={doc.title}>
        <h3 className="doc-card__title">{doc.title}</h3>
        <p className="doc-card__meta">
          {doc.correspondent_name ?? "Unbekannt"}
          {doc.document_type_name ? ` · ${doc.document_type_name}` : ""}
        </p>
        <p className="doc-card__footer">
          <span className="doc-card__date">
            {new Date(doc.added_at).toLocaleDateString("de-DE")}
          </span>
          <ProcessingBadge state={doc.processing_state} />
        </p>
      </button>
      <div className="triage-card__assign">
        <label className="filter">
          <span>Owner setzen</span>
          <select
            value={userId === "" ? "" : String(userId)}
            onChange={(e) =>
              setUserId(e.target.value ? Number(e.target.value) : "")
            }
            disabled={saving || users.length === 0}
          >
            <option value="">Nutzer wählen …</option>
            {users.map((u) => (
              <option key={u.id} value={String(u.id)}>
                {u.username}
              </option>
            ))}
          </select>
        </label>
        <button onClick={assign} disabled={userId === "" || saving}>
          {saving ? "Zuweisen …" : "Zuweisen"}
        </button>
        {error && <p className="status status--error">{error}</p>}
      </div>
    </div>
  );
}

