import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { DEFAULT_VIEW, pathToView, viewToPath } from "../viewRoutes";
import {
  autoFileBatch,
  bulkClassifyDocuments,
  bulkUpdateDocuments,
  createSavedView,
  createCorrespondent,
  createDocumentType,
  createFolder,
  createStoragePath,
  createTag,
  deleteSavedView,
  getCorrespondents,
  getCustomFields,
  getDocuments,
  getDocumentThumbnail,
  getDocumentTypes,
  getFolders,
  getMe,
  getSavedViews,
  getStoragePaths,
  getTags,
  getUsers,
  logout,
  setDocumentOwner,
  updateSavedView,
  updateDocument,
  type CustomField,
  type DocumentItem,
  type FolderRef,
  type Me,
  type NamedRef,
  type ProcessingStateFilter,
  type ReviewStatus,
  type SavedView,
  type SavedViewQuery,
  type TagRef,
  type User,
} from "../api";
import { toCanonicalValue } from "../customFields";
import { sanitizeSnippet } from "../sanitize";
import { ProcessingBadge } from "./ProcessingStatus";
import DuplicateReportModal from "./DuplicateReportModal";
import SemanticSearchPanel from "./SemanticSearchPanel";
import UploadZone from "./UploadZone";
import MobileCapture from "./MobileCapture";
// Code-Splitting (Perf, #8): die großen, sich gegenseitig ausschließenden
// Seiten-Views werden lazy geladen (eigene Chunks statt alles im Haupt-Bundle).
// Beim ersten Öffnen einer View lädt Vite deren Chunk nach; die Suspense-Grenze
// unten zeigt solange einen Ladehinweis.
const CaseFilesPage = lazy(() => import("./CaseFilesPage"));
const DossiersPage = lazy(() => import("./DossiersPage"));
const ContractsPage = lazy(() => import("./ContractsPage"));
const CopilotPage = lazy(() => import("./CopilotPage"));
const DocumentDetail = lazy(() => import("./DocumentDetail"));
const KnowledgeGraphPage = lazy(() => import("./KnowledgeGraphPage"));
const RulesPage = lazy(() => import("./RulesPage"));
const DuePage = lazy(() => import("./DuePage"));
const WorkflowsPage = lazy(() => import("./WorkflowsPage"));
const CustomFieldsAdmin = lazy(() => import("./CustomFieldsAdmin"));
const MailCenterPage = lazy(() => import("./MailCenterPage"));
const SystemStatusPage = lazy(() => import("./SystemStatusPage"));
const InboxPage = lazy(() => import("./InboxPage"));
const EvidenceCenterPage = lazy(() => import("./EvidenceCenterPage"));
const QualityCenterPage = lazy(() => import("./QualityCenterPage"));
const DashboardPage = lazy(() => import("./DashboardPage"));
import CommandPalette, {
  type CommandPreset,
  type CommandView,
} from "./CommandPalette";

// Von-/Bis-Eingaben eines CURRENCY-Zusatzfeld-Filters (STOAA-113).
type CurrencyRange = { gte: string; lte: string };
type FolderFilterValue = number | "none" | "";
type WorkspaceMode = "cards" | "compact";
type WorkspacePreset = CommandPreset;
type MainView = CommandView;

// Muss dem Backend entsprechen (DRF PageNumberPagination, config/settings.py:
// REST_FRAMEWORK["PAGE_SIZE"] = 25). Nur für die Anzeige „Seite X von N" nötig;
// die Rand-Buttons werden zusätzlich über next/previous der Antwort abgesichert.
const PAGE_SIZE = 25;

// Der Speicherpfad-Filter nutzt den Backend-Query-Param `storage_path`
// (Kind-Ticket STOAA-49, PR #29 gemergt → DocumentViewSet.get_queryset filtert
// via `storage_path_id`). Der Abschnitt ist damit voll funktionsfähig aktiviert.
const STORAGE_PATH_FILTER_ENABLED = true;
const PROCESSING_VIEW_VALUES = new Set(["failed", "processing", "ready", "retry_pending"]);

function toNumberOrEmpty(value: unknown): number | "" {
  if (value === "" || value === null || value === undefined) return "";
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : "";
}

function toFolderFilterValue(value: unknown): FolderFilterValue {
  if (value === "none") return "none";
  return toNumberOrEmpty(value);
}

function toProcessingFilterValue(value: unknown): ProcessingStateFilter | "" {
  return typeof value === "string" && PROCESSING_VIEW_VALUES.has(value)
    ? (value as ProcessingStateFilter)
    : "";
}

function customFiltersToCurrencyRanges(
  filters: Record<string, string> | undefined,
): Record<number, CurrencyRange> {
  const out: Record<number, CurrencyRange> = {};
  for (const [key, value] of Object.entries(filters ?? {})) {
    const match = /^custom_field_(\d+)_(gte|lte)$/.exec(key);
    if (!match) continue;
    const fieldId = Number(match[1]);
    const bound = match[2] as keyof CurrencyRange;
    const current = out[fieldId] ?? { gte: "", lte: "" };
    out[fieldId] = { ...current, [bound]: value };
  }
  return out;
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, item]) => [key, stableValue(item)]),
    );
  }
  return value;
}

function savedViewKey(query: SavedViewQuery): string {
  return JSON.stringify(stableValue(query));
}

// Kompaktes „…"-Overflow-Menü für selten genutzte Aktionen der Topbar
// (STOAA-417). Reines UI-Element (kein neues Verhalten): Klick öffnet ein
// Panel, ein transparenter Backdrop schließt es wieder (Klick außerhalb).
function OverflowMenu({
  label = "Weitere Aktionen",
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="overflow-menu">
      <button
        type="button"
        className="overflow-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title={label}
        onClick={() => setOpen((o) => !o)}
      >
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path
            fill="currentColor"
            d="M6 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4m6 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4m6 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4"
          />
        </svg>
      </button>
      {open && (
        <>
          <div
            className="overflow-menu__backdrop"
            aria-hidden="true"
            onClick={() => setOpen(false)}
          />
          <div className="overflow-menu__panel" role="menu">
            {children}
          </div>
        </>
      )}
    </div>
  );
}

export default function DocumentsPage({ onLogout }: { onLogout: () => void }) {
  const [q, setQ] = useState("");
  const [semanticOpen, setSemanticOpen] = useState(false);
  const [dupReportOpen, setDupReportOpen] = useState(false);
  const [autoFileBusy, setAutoFileBusy] = useState(false);
  const [autoFileNote, setAutoFileNote] = useState<string | null>(null);
  const [correspondent, setCorrespondent] = useState<number | "">("");
  const [documentType, setDocumentType] = useState<number | "">("");
  const [tag, setTag] = useState<number | "">("");
  // Speicherpfad-Filter (STOAA-50). Bis der Backend-Query-Param gemergt ist,
  // bleibt der Speicherpfad-Abschnitt in der Sidebar ausgegraut (no-op).
  const [storagePath, setStoragePath] = useState<number | "">("");
  const [folder, setFolder] = useState<FolderFilterValue>("");
  // Verarbeitungsstatus-Filter (STOAA-249): leer = kein Filter, sonst UI-Bucket.
  const [processingState, setProcessingState] = useState<ProcessingStateFilter | "">("");
  const [sharedScope, setSharedScope] = useState<"" | "with-me" | "by-me">("");
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
  const [folders, setFolders] = useState<FolderRef[]>([]);
  const [draggingDocumentId, setDraggingDocumentId] = useState<number | null>(null);
  const [folderDropBusy, setFolderDropBusy] = useState<number | null>(null);
  const [folderDropError, setFolderDropError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkFolder, setBulkFolder] = useState("");
  const [bulkDocumentType, setBulkDocumentType] = useState("");
  const [bulkCorrespondent, setBulkCorrespondent] = useState("");
  const [bulkReviewStatus, setBulkReviewStatus] = useState("");
  const [bulkAddTag, setBulkAddTag] = useState("");
  const [bulkRemoveTag, setBulkRemoveTag] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [bulkMessage, setBulkMessage] = useState<string | null>(null);
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
  // Navigation über die URL (#7, Stage 1 + 1b): sowohl das geöffnete Dokument
  // (/dokument/:id) als auch die aktive View (/inbox, /dashboard, …) stehen in
  // der URL. selectedId/view werden daraus abgeleitet; setSelectedId/setView
  // navigieren nur noch – die bestehenden Aufrufstellen bleiben unverändert.
  const routerNavigate = useNavigate();
  const routerLocation = useLocation();
  const _docMatch = routerLocation.pathname.match(/^\/dokument\/(\d+)/);
  const selectedId = _docMatch ? Number(_docMatch[1]) : null;
  // Letzte echte View merken: die /dokument/:id-URL trägt selbst keine View, das
  // Schließen eines Dokuments soll aber dorthin zurückkehren, wo man war.
  const lastViewRef = useRef<MainView>(DEFAULT_VIEW);
  const view: MainView =
    selectedId !== null ? lastViewRef.current : pathToView(routerLocation.pathname);
  if (selectedId === null) lastViewRef.current = view;
  const setView = (v: MainView) => {
    routerNavigate(viewToPath(v));
  };
  const setSelectedId = (id: number | null) => {
    if (id == null) {
      // Nur navigieren, wenn wirklich ein Dokument offen ist → zurück zur View.
      if (selectedId !== null) routerNavigate(viewToPath(lastViewRef.current));
    } else {
      routerNavigate(`/dokument/${id}`);
    }
  };
  const [selectedPage, setSelectedPage] = useState<number | null>(null);
  const [previewId, setPreviewId] = useState<number | null>(null);
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>(() => {
    try {
      return localStorage.getItem("dms.workspace.mode") === "compact"
        ? "compact"
        : "cards";
    } catch {
      return "cards";
    }
  });
  // (Aktive Hauptansicht `view`/`setView` kommen jetzt aus der URL – siehe oben.)
  const [commandOpen, setCommandOpen] = useState(false);
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [savedViewsBusy, setSavedViewsBusy] = useState(false);
  const [savedViewsError, setSavedViewsError] = useState<string | null>(null);
  const savedDefaultAppliedRef = useRef(false);
  // Sidebar auf schmalen Screens ein-/ausklappbar.
  const [navOpen, setNavOpen] = useState(false);
  // Desktop-Sidebar auf Icon-only einklappbar; Zustand persistent (localStorage).
  const [navCollapsed, setNavCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem("dms.sidebar.collapsed") === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("dms.sidebar.collapsed", navCollapsed ? "1" : "0");
    } catch {
      /* localStorage optional (z. B. Privatmodus) – kein harter Fehler */
    }
  }, [navCollapsed]);
  useEffect(() => {
    try {
      localStorage.setItem("dms.workspace.mode", workspaceMode);
    } catch {
      /* localStorage optional (z. B. Privatmodus) – kein harter Fehler */
    }
  }, [workspaceMode]);

  function loadSavedViews() {
    setSavedViewsBusy(true);
    setSavedViewsError(null);
    getSavedViews()
      .then(setSavedViews)
      .catch((err) => {
        setSavedViewsError(
          err instanceof Error ? err.message : "Gespeicherte Ansichten konnten nicht geladen werden.",
        );
      })
      .finally(() => setSavedViewsBusy(false));
  }

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
      getFolders(),
    ])
      .then(([c, d, t, s, f]) => {
        setCorrespondents(c);
        setDocumentTypes(d);
        setTags(t);
        setStoragePaths(s);
        setFolders(f);
      })
      .catch(() => {
        /* Stammdaten sind optional – Fehler hier nicht blockierend */
      });
    loadCustomFields();
  }, []);

  useEffect(() => {
    loadSavedViews();
    // reloadKey aktualisiert nach Uploads/Massenänderungen auch die Count-Badges.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

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
  async function addFolder(name: string) {
    const item = await createFolder(name);
    setFolders((prev) =>
      [...prev, item].sort((a, b) => a.full_path.localeCompare(b.full_path)),
    );
    return { id: item.id, name: item.full_path };
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
  const currentSavedQuery = useMemo<SavedViewQuery>(() => {
    const query: SavedViewQuery = {};
    if (q.trim()) query.q = q.trim();
    if (correspondent) query.correspondent = correspondent;
    if (documentType) query.document_type = documentType;
    if (tag) query.tag = tag;
    if (storagePath) query.storage_path = storagePath;
    if (folder) query.folder = folder;
    if (processingState) query.processing_state = processingState;
    if (ordering) query.ordering = ordering;
    if (Object.keys(customFilters).length > 0) query.customFilters = customFilters;
    return query;
  }, [
    q,
    correspondent,
    documentType,
    tag,
    storagePath,
    folder,
    processingState,
    ordering,
    customFilterKey,
    customFilters,
  ]);
  const activeSavedViewId = useMemo(() => {
    const currentKey = savedViewKey(currentSavedQuery);
    return savedViews.find((item) => savedViewKey(item.query) === currentKey)?.id ?? null;
  }, [currentSavedQuery, savedViews]);
  const activeSavedView = savedViews.find((item) => item.id === activeSavedViewId) ?? null;

  useEffect(() => {
    // AbortController (#8): eine veraltete Listen-/Suchanfrage wird beim
    // nächsten Dep-Wechsel (Tippen/Filter/Seite) wirklich abgebrochen, nicht
    // nur ignoriert – spart Backend-Last und vermeidet Races.
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getDocuments(
      {
        q: debouncedQ,
        correspondent,
        document_type: documentType,
        tag,
        storage_path: storagePath,
        folder,
        processing_state: processingState,
        shared: sharedScope,
        // Triage nur für Admins anfordern; das Backend ignoriert den Param für
        // Normalnutzer ohnehin, aber so bleibt die FE-Absicht eindeutig.
        owner: triage && me?.is_dms_admin ? "none" : "",
        ordering,
        page,
        customFilters,
      },
      ctrl.signal,
    )
      .then((res) => {
        setDocs(res.results);
        setCount(res.count);
        setHasNext(res.next !== null);
        setHasPrev(res.previous !== null);
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return; // abgebrochene Anfrage: still verwerfen
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
    // customFilterKey serialisiert customFilters für einen stabilen Dep-Vergleich.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ, correspondent, documentType, tag, storagePath, folder, processingState, sharedScope, triage, me?.is_dms_admin, ordering, page, reloadKey, customFilterKey]);

  useEffect(() => {
    if (view !== "docs" || triage) return;
    if (docs.length === 0) {
      setPreviewId(null);
      return;
    }
    if (previewId === null || !docs.some((doc) => doc.id === previewId)) {
      setPreviewId(docs[0].id);
    }
  }, [docs, previewId, triage, view]);

  // Sichtbarkeit von „Zurücksetzen" & Empty-State-Text: alle roh getippten Filter.
  const hasCurrencyInput = useMemo(
    () =>
      Object.values(currencyFilters).some((r) => r.gte?.trim() || r.lte?.trim()),
    [currencyFilters],
  );
  const hasFilters = useMemo(
    () =>
      !!(debouncedQ || correspondent || documentType || tag || storagePath || processingState || sharedScope) ||
      !!folder ||
      hasCurrencyInput,
    [debouncedQ, correspondent, documentType, tag, storagePath, folder, processingState, sharedScope, hasCurrencyInput],
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
  function onFolderChange(v: FolderFilterValue) {
    setFolder(v);
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
  function applyWorkspacePreset(preset: WorkspacePreset) {
    setSelectedIds(new Set());
    setBulkError(null);
    setBulkMessage(null);
    if (preset === "inbox") {
      setView("inbox");
      return;
    }
    if (preset === "quality") {
      setView("quality");
      return;
    }

    setQ("");
    setCorrespondent("");
    setDocumentType("");
    setTag("");
    setStoragePath("");
    setFolder("");
    setProcessingState("");
    setOrdering("");
    setCurrencyFilters({});
    setTriage(false);
    setPage(1);
    setView("docs");

    if (preset === "latest") {
      setOrdering("-added_at");
    } else if (preset === "processing") {
      setProcessingState("processing");
    } else if (preset === "failed") {
      setProcessingState("failed");
    } else if (preset === "unfiled") {
      setFolder("none");
    }
  }
  const activeWorkspacePreset: WorkspacePreset | "custom" =
    processingState === "processing"
      ? "processing"
      : processingState === "failed"
        ? "failed"
        : folder === "none"
          ? "unfiled"
          : !triage && !hasFilters && (ordering === "" || ordering === "-added_at")
            ? "latest"
            : "custom";
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
    setFolder("");
    setProcessingState("");
    setSharedScope("");
    setOrdering("");
    setCurrencyFilters({});
    setPage(1);
  }
  function resetWorkspace() {
    resetFilters();
    setTriage(false);
    setSelectedIds(new Set());
    setBulkError(null);
    setBulkMessage(null);
  }

  function applySavedView(savedView: SavedView) {
    const query = savedView.query ?? {};
    setSelectedId(null);
    setSelectedPage(null);
    setSelectedIds(new Set());
    setBulkError(null);
    setBulkMessage(null);
    setQ(String(query.q ?? ""));
    setCorrespondent(toNumberOrEmpty(query.correspondent));
    setDocumentType(toNumberOrEmpty(query.document_type));
    setTag(toNumberOrEmpty(query.tag));
    setStoragePath(toNumberOrEmpty(query.storage_path));
    setFolder(toFolderFilterValue(query.folder));
    setProcessingState(toProcessingFilterValue(query.processing_state));
    setOrdering(typeof query.ordering === "string" ? query.ordering : "");
    setCurrencyFilters(customFiltersToCurrencyRanges(query.customFilters));
    setTriage(false);
    setPage(1);
    setView("docs");
    setNavOpen(false);
  }

  useEffect(() => {
    if (savedDefaultAppliedRef.current || savedViews.length === 0) return;
    savedDefaultAppliedRef.current = true;
    const defaultView = savedViews.find((item) => item.is_default);
    if (defaultView) applySavedView(defaultView);
    // Der Default soll genau einmal nach dem ersten Laden angewendet werden.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedViews]);

  function suggestSavedViewName() {
    if (q.trim()) return `Suche: ${q.trim()}`;
    if (folder === "none") return "Ohne Ordner";
    if (processingState === "failed") return "Fehlerhafte Verarbeitung";
    if (processingState === "processing") return "In Verarbeitung";
    if (tag) return tags.find((item) => item.id === tag)?.name ?? "Tag-Ansicht";
    if (correspondent) {
      return (
        correspondents.find((item) => item.id === correspondent)?.name ??
        "Korrespondent-Ansicht"
      );
    }
    return "Meine Ansicht";
  }

  async function saveCurrentView() {
    const name = window.prompt("Name der gespeicherten Ansicht", suggestSavedViewName());
    if (!name?.trim()) return;

    setSavedViewsBusy(true);
    setSavedViewsError(null);
    try {
      await createSavedView({
        name: name.trim(),
        query: currentSavedQuery,
      });
      loadSavedViews();
    } catch (err) {
      setSavedViewsError(
        err instanceof Error ? err.message : "Ansicht konnte nicht gespeichert werden.",
      );
    } finally {
      setSavedViewsBusy(false);
    }
  }

  async function toggleDefaultSavedView(savedView: SavedView) {
    setSavedViewsBusy(true);
    setSavedViewsError(null);
    try {
      await updateSavedView(savedView.id, { is_default: !savedView.is_default });
      loadSavedViews();
    } catch (err) {
      setSavedViewsError(
        err instanceof Error ? err.message : "Startansicht konnte nicht geändert werden.",
      );
    } finally {
      setSavedViewsBusy(false);
    }
  }

  async function removeSavedView(savedView: SavedView) {
    if (!window.confirm(`Gespeicherte Ansicht "${savedView.name}" löschen?`)) return;
    setSavedViewsBusy(true);
    setSavedViewsError(null);
    try {
      await deleteSavedView(savedView.id);
      loadSavedViews();
    } catch (err) {
      setSavedViewsError(
        err instanceof Error ? err.message : "Ansicht konnte nicht gelöscht werden.",
      );
    } finally {
      setSavedViewsBusy(false);
    }
  }

  async function moveDocumentToFolder(documentId: number, targetFolder: number | null) {
    const current = docs.find((doc) => doc.id === documentId);
    if (current?.folder === targetFolder) return;

    setFolderDropError(null);
    setFolderDropBusy(documentId);
    try {
      const updated = await updateDocument(documentId, { folder: targetFolder });
      setDocs((prev) =>
        prev.map((doc) =>
          doc.id === documentId
            ? {
                ...doc,
                folder: updated.folder,
                folder_name: updated.folder_name,
                folder_path: updated.folder_path,
              }
            : doc,
        ),
      );
      setFolders(await getFolders());
      // Wenn gerade ein Ordnerfilter aktiv ist, muss die Karte beim Verschieben
      // ggf. aus der aktuellen Ansicht verschwinden. Die lokale Aktualisierung
      // gibt sofort Feedback, der Reload bringt Count/Pagination wieder exakt.
      setReloadKey((k) => k + 1);
    } catch (err) {
      setFolderDropError(
        err instanceof Error ? err.message : "Dokument konnte nicht verschoben werden.",
      );
    } finally {
      setFolderDropBusy(null);
      setDraggingDocumentId(null);
    }
  }

  function toggleSelected(documentId: number, selected: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (selected) next.add(documentId);
      else next.delete(documentId);
      return next;
    });
    setBulkError(null);
    setBulkMessage(null);
  }

  function selectCurrentPage() {
    setSelectedIds(new Set(docs.map((doc) => doc.id)));
    setBulkError(null);
    setBulkMessage(null);
  }

  function clearSelection() {
    setSelectedIds(new Set());
    setBulkError(null);
    setBulkMessage(null);
  }

  function _bulkNumber(value: string): number | null | undefined {
    if (!value) return undefined;
    if (value === "__none") return null;
    return Number(value);
  }

  async function applyBulkUpdate() {
    const ids = Array.from(selectedIds);
    const set: {
      folder?: number | null;
      document_type?: number | null;
      correspondent?: number | null;
      review_status?: ReviewStatus;
    } = {};
    const folderValue = _bulkNumber(bulkFolder);
    const documentTypeValue = _bulkNumber(bulkDocumentType);
    const correspondentValue = _bulkNumber(bulkCorrespondent);
    if (folderValue !== undefined) set.folder = folderValue;
    if (documentTypeValue !== undefined) set.document_type = documentTypeValue;
    if (correspondentValue !== undefined) set.correspondent = correspondentValue;
    if (bulkReviewStatus) set.review_status = bulkReviewStatus as ReviewStatus;

    const add_tags = bulkAddTag ? [Number(bulkAddTag)] : [];
    const remove_tags = bulkRemoveTag ? [Number(bulkRemoveTag)] : [];
    if (
      Object.keys(set).length === 0 &&
      add_tags.length === 0 &&
      remove_tags.length === 0
    ) {
      setBulkError("Wähle mindestens eine Änderung aus.");
      return;
    }

    setBulkBusy(true);
    setBulkError(null);
    setBulkMessage(null);
    try {
      const result = await bulkUpdateDocuments(ids, { set, add_tags, remove_tags });
      setBulkMessage(
        `${result.updated} Dokument${result.updated === 1 ? "" : "e"} aktualisiert${
          result.errors.length ? `, ${result.errors.length} übersprungen` : ""
        }.`,
      );
      setSelectedIds(new Set());
      setBulkFolder("");
      setBulkDocumentType("");
      setBulkCorrespondent("");
      setBulkReviewStatus("");
      setBulkAddTag("");
      setBulkRemoveTag("");
      setFolders(await getFolders());
      setReloadKey((k) => k + 1);
    } catch (err) {
      setBulkError(err instanceof Error ? err.message : "Massenänderung fehlgeschlagen.");
    } finally {
      setBulkBusy(false);
    }
  }

  async function applyBulkClassify() {
    const ids = Array.from(selectedIds);
    setBulkBusy(true);
    setBulkError(null);
    setBulkMessage(null);
    try {
      const result = await bulkClassifyDocuments(ids);
      if (result.task_id) {
        setBulkMessage(`Reklassifizierung läuft im Hintergrund (${ids.length} Dokumente).`);
      } else {
        setBulkMessage(
          `${result.updated} aktualisiert, ${result.unchanged ?? 0} unverändert${
            result.errors.length ? `, ${result.errors.length} übersprungen` : ""
          }.`,
        );
      }
      setSelectedIds(new Set());
      setFolders(await getFolders());
      setReloadKey((k) => k + 1);
    } catch (err) {
      setBulkError(err instanceof Error ? err.message : "Reklassifizierung fehlgeschlagen.");
    } finally {
      setBulkBusy(false);
    }
  }

  function handleLogout() {
    logout();
    onLogout();
  }

  const previewDoc = previewId ? docs.find((doc) => doc.id === previewId) ?? null : null;

  const navigate = (v: MainView) => {
    setView(v);
    setNavOpen(false); // Overlay auf Mobil nach Auswahl schließen
  };

  function navigateFromCommand(v: MainView) {
    setSelectedId(null);
    setSelectedPage(null);
    navigate(v);
    setCommandOpen(false);
  }

  function applyPresetFromCommand(preset: WorkspacePreset) {
    setSelectedId(null);
    setSelectedPage(null);
    applyWorkspacePreset(preset);
    setCommandOpen(false);
  }

  function openDocumentFromCommand(documentId: number) {
    setSelectedPage(null);
    setSelectedId(documentId);
    setCommandOpen(false);
  }

  async function runAutoFileBatch() {
    setAutoFileBusy(true);
    setAutoFileNote(null);
    try {
      const res = await autoFileBatch();
      if (res.processed === 0) {
        setAutoFileNote("Kein unabgelegtes Dokument gefunden.");
      } else if (res.filed === 0) {
        setAutoFileNote(
          `Kein ausreichend sicherer Vorschlag für ${res.processed} unabgelegte(s) Dokument(e).`,
        );
      } else {
        setAutoFileNote(
          `${res.filed} von ${res.processed} Dokument(en) automatisch einsortiert.`,
        );
      }
      setReloadKey((k) => k + 1);
    } catch {
      setAutoFileNote("Aufräumen fehlgeschlagen.");
    } finally {
      setAutoFileBusy(false);
    }
  }

  if (selectedId !== null) {
    return (
      <>
        <Suspense fallback={<div className="muted">Lade Dokument …</div>}>
          <DocumentDetail
            id={selectedId}
          initialPage={selectedPage}
          onBack={() => {
            setSelectedId(null);
            setSelectedPage(null);
            setReloadKey((k) => k + 1); // ggf. geänderte Metadaten in der Liste zeigen
          }}
          correspondents={correspondents}
          documentTypes={documentTypes}
          storagePaths={storagePaths}
          folders={folders.map((f) => ({ id: f.id, name: f.full_path }))}
          allTags={tags}
          customFields={customFields}
          canEdit={!!me?.can_write}
          onCreateCorrespondent={addCorrespondent}
          onCreateDocumentType={addDocumentType}
          onCreateStoragePath={addStoragePath}
          onCreateFolder={addFolder}
          onCreateTag={addTag}
          onOpenDocument={(docId, pageNo) => {
            setSelectedId(docId);
            setSelectedPage(pageNo ?? null);
          }}
          onManageFields={
            me?.is_dms_admin
              ? () => {
                setSelectedId(null);
                setSelectedPage(null);
                setView("fields");
              }
              : undefined
          }
          />
        </Suspense>
        <CommandPalette
          open={commandOpen}
          onOpenChange={setCommandOpen}
          canWrite={!!me?.can_write}
          isAdmin={!!me?.is_dms_admin}
          savedViews={savedViews}
          onNavigate={navigateFromCommand}
          onApplyPreset={applyPresetFromCommand}
          onApplySavedView={applySavedView}
          onOpenDocument={openDocumentFromCommand}
        />
      </>
    );
  }

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
        collapsed={navCollapsed}
        onToggleCollapse={() => setNavCollapsed((c) => !c)}
        correspondents={correspondents}
        tags={tags}
        documentTypes={documentTypes}
        storagePaths={storagePaths}
        folders={folders}
        correspondent={correspondent}
        tag={tag}
        documentType={documentType}
        storagePath={storagePath}
        folder={folder}
        processingState={processingState}
        onCorrespondentChange={onCorrespondentChange}
        onTagChange={onTagChange}
        onDocumentTypeChange={onDocumentTypeChange}
        onStoragePathChange={onStoragePathChange}
        onFolderChange={onFolderChange}
        draggingDocumentId={draggingDocumentId}
        folderDropBusy={folderDropBusy}
        folderDropError={folderDropError}
        onDocumentDropToFolder={moveDocumentToFolder}
        onProcessingStateChange={onProcessingStateChange}
        storagePathEnabled={STORAGE_PATH_FILTER_ENABLED}
        currencyFields={currencyFields}
        currencyFilters={currencyFilters}
        onCurrencyChange={onCurrencyChange}
        savedViews={savedViews}
        activeSavedViewId={activeSavedViewId}
        savedViewsBusy={savedViewsBusy}
        savedViewsError={savedViewsError}
        onSavedViewSelect={applySavedView}
        onSavedViewDefault={toggleDefaultSavedView}
        onSavedViewDelete={removeSavedView}
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
            {view === "dashboard"
              ? "Cockpit"
              : view === "capture"
              ? "Erfassen"
              : view === "cases"
                ? "Akten"
              : view === "dossiers"
                ? "Dossiers"
              : view === "contracts"
                ? "Verträge"
              : view === "knowledge"
                ? "Gedächtnis"
              : view === "copilot"
                ? "Copilot"
              : view === "inbox"
                ? "Inbox"
              : view === "rules"
                ? "Regeln"
                : view === "workflows"
                  ? "Workflows"
                  : view === "fields"
                    ? "Zusatzfelder"
                    : view === "mail"
                      ? "E-Mail"
                      : view === "evidence"
                        ? "Beweis-Center"
                      : view === "quality"
                        ? "Qualität"
                      : view === "system"
                        ? "Systemstatus"
                      : view === "faellig"
                        ? "Wiedervorlage"
                        : "Dokumente"}
          </h1>
          <button
            type="button"
            className="command-trigger"
            aria-label="Command Palette öffnen"
            title="Suchen und Aktionen"
            onClick={() => setCommandOpen(true)}
          >
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path
                fill="currentColor"
                d="M10 4a6 6 0 0 1 4.8 9.6l4.3 4.3-1.4 1.4-4.3-4.3A6 6 0 1 1 10 4m0 2a4 4 0 1 0 0 8 4 4 0 0 0 0-8"
              />
            </svg>
          </button>
          {view === "docs" && (
            <>
              <input
                className="search topbar-search"
                placeholder="Volltextsuche (Titel & Inhalt) …"
                value={q}
                onChange={(e) => onSearchChange(e.target.value)}
              />
              <button
                type="button"
                className="topbar-semantic"
                onClick={() => setSemanticOpen(true)}
                title="Smart-Suche – Volltext + Bedeutung fusioniert"
              >
                🔎 Smart-Suche
              </button>
              {semanticOpen && (
                <SemanticSearchPanel
                  initialQuery={q}
                  onClose={() => setSemanticOpen(false)}
                  onOpenDocument={(id) => {
                    setSemanticOpen(false);
                    openDocumentFromCommand(id);
                  }}
                />
              )}
              {dupReportOpen && (
                <DuplicateReportModal
                  onClose={() => setDupReportOpen(false)}
                  onOpenDocument={openDocumentFromCommand}
                />
              )}
              {/* Selten genutzte Aktionen (Sortierung, Zurücksetzen, Triage)
                  gebündelt im „…"-Menü, damit die Suche prominent bleibt. */}
              <OverflowMenu>
                <label className="overflow-menu__field">
                  <span>Sortierung</span>
                  <select
                    value={ordering}
                    onChange={(e) => onOrderingChange(e.target.value)}
                  >
                    <option value="">Standard</option>
                    <option value="-added_at">Datum (neu → alt)</option>
                    <option value="added_at">Datum (alt → neu)</option>
                    <option value="title">Titel (A–Z)</option>
                    <option value="-title">Titel (Z–A)</option>
                  </select>
                </label>
                <label className="overflow-menu__field">
                  <span>Familien-Freigabe</span>
                  <select
                    value={sharedScope}
                    onChange={(e) => {
                      setSharedScope(e.target.value as "" | "with-me" | "by-me");
                      setPage(1);
                    }}
                  >
                    <option value="">Alle Dokumente</option>
                    <option value="with-me">Mit mir geteilt</option>
                    <option value="by-me">Von mir geteilt</option>
                  </select>
                </label>
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
                {me?.can_write && (
                  <button
                    type="button"
                    className="link"
                    onClick={runAutoFileBatch}
                    disabled={autoFileBusy}
                    title="Ordnerlose Dokumente per Auto-Ablage vorsortieren"
                  >
                    {autoFileBusy ? "Räume auf …" : "🗂️ Posteingang aufräumen"}
                  </button>
                )}
                <button
                  type="button"
                  className="link"
                  onClick={() => setDupReportOpen(true)}
                  title="Inhaltliche Duplikate im Bestand finden"
                >
                  🔎 Dubletten finden
                </button>
                {hasFilters && (
                  <button className="link" onClick={resetFilters}>
                    Filter zurücksetzen
                  </button>
                )}
              </OverflowMenu>
            </>
          )}
        </header>
        {autoFileNote && (
          <div className="auto-file-banner" role="status">
            <span>{autoFileNote}</span>
            <button
              type="button"
              className="link"
              onClick={() => setAutoFileNote(null)}
              aria-label="Schließen"
            >
              ✕
            </button>
          </div>
        )}

        <div className="content-body">
          <Suspense fallback={<div className="muted">Lade Ansicht …</div>}>
          {view === "dashboard" ? (
            <DashboardPage
              canWrite={!!me?.can_write}
              isAdmin={!!me?.is_dms_admin}
              onNavigate={navigate}
              onOpenDocument={(docId) => setSelectedId(docId)}
            />
          ) : view === "copilot" ? (
            <CopilotPage
              folders={folders}
              onOpenDocument={(docId, pageNo) => {
                setSelectedId(docId);
                setSelectedPage(pageNo ?? null);
              }}
            />
          ) : view === "cases" ? (
            <CaseFilesPage
              canEdit={!!me?.can_write}
              onOpenDocument={(docId, pageNo) => {
                setSelectedId(docId);
                setSelectedPage(pageNo ?? null);
              }}
            />
          ) : view === "dossiers" ? (
            <DossiersPage
              canEdit={!!me?.can_write}
              onOpenDocument={(docId, pageNo) => {
                setSelectedId(docId);
                setSelectedPage(pageNo ?? null);
              }}
            />
          ) : view === "contracts" ? (
            <ContractsPage
              canEdit={!!me?.can_write}
              onOpenDocument={(docId) => setSelectedId(docId)}
            />
          ) : view === "knowledge" ? (
            <KnowledgeGraphPage
              canEdit={!!me?.can_write}
              onOpenDocument={(docId) => setSelectedId(docId)}
            />
          ) : view === "rules" ? (
            <RulesPage canEdit={!!me?.can_write} />
          ) : view === "inbox" ? (
            <InboxPage
              canEdit={!!me?.can_write}
              onOpenDocument={(docId) => setSelectedId(docId)}
            />
          ) : view === "workflows" ? (
            <WorkflowsPage canEdit={!!me?.can_write} />
          ) : view === "fields" ? (
            <CustomFieldsAdmin
              canEdit={!!me?.can_write}
              onChanged={loadCustomFields}
            />
          ) : view === "mail" ? (
            <MailCenterPage
              canEdit={!!me?.can_write}
              onOpenDocument={(docId) => setSelectedId(docId)}
            />
          ) : view === "evidence" ? (
            <EvidenceCenterPage onOpenDocument={(docId) => setSelectedId(docId)} />
          ) : view === "quality" ? (
            <QualityCenterPage onOpenDocument={(docId) => setSelectedId(docId)} />
          ) : view === "system" ? (
            <SystemStatusPage />
          ) : view === "capture" ? (
            <MobileCapture
              canWrite={!!me?.can_write}
              onUploaded={() => {
                // Nach erfolgreicher Erfassung die Liste frisch ziehen, damit das
                // neue Dokument sichtbar wird, sobald man zurück wechselt.
                setPage(1);
                setReloadKey((k) => k + 1);
              }}
            />
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

              {/* Stammdaten-Filter leben in der Sidebar (STOAA-50). Der Workspace
                  legt darüber kuratierte Schnellansichten, Karten-/Kompaktmodus
                  und eine permanente Dokumentvorschau. */}
              <section className="workspace">
                <WorkspaceToolbar
                  count={count}
                  selectedCount={selectedIds.size}
                  loading={loading}
                  activePreset={activeWorkspacePreset}
                  activeSavedViewName={activeSavedView?.name ?? ""}
                  mode={workspaceMode}
                  hasFilters={hasFilters || triage}
                  saveViewBusy={savedViewsBusy}
                  onPreset={applyWorkspacePreset}
                  onModeChange={setWorkspaceMode}
                  onReset={resetWorkspace}
                  onSaveView={saveCurrentView}
                />
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
                    {selectedIds.size > 0 && (
                      <BulkActionBar
                        selectedCount={selectedIds.size}
                        correspondents={correspondents}
                        documentTypes={documentTypes}
                        folders={folders}
                        tags={tags}
                        folder={bulkFolder}
                        documentType={bulkDocumentType}
                        correspondent={bulkCorrespondent}
                        reviewStatus={bulkReviewStatus}
                        addTag={bulkAddTag}
                        removeTag={bulkRemoveTag}
                        busy={bulkBusy}
                        error={bulkError}
                        message={bulkMessage}
                        onFolderChange={setBulkFolder}
                        onDocumentTypeChange={setBulkDocumentType}
                        onCorrespondentChange={setBulkCorrespondent}
                        onReviewStatusChange={setBulkReviewStatus}
                        onAddTagChange={setBulkAddTag}
                        onRemoveTagChange={setBulkRemoveTag}
                        onSelectPage={selectCurrentPage}
                        onClear={clearSelection}
                        onApply={applyBulkUpdate}
                        onClassify={applyBulkClassify}
                      />
                    )}
                    <div className={`workspace-shell${triage ? " workspace-shell--single" : ""}`}>
                      <div className="workspace-list-pane">
                        <p className="muted result-count">
                          {count} {count === 1 ? "Dokument" : "Dokumente"}
                        </p>
                        <div className={`doc-grid doc-grid--${workspaceMode}`}>
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
                                selected={selectedIds.has(d.id)}
                                previewed={d.id === previewId}
                                mode={workspaceMode}
                                onPreview={() => setPreviewId(d.id)}
                                onOpen={() => setSelectedId(d.id)}
                                onSelectedChange={(checked) => toggleSelected(d.id, checked)}
                                onDragStart={() => {
                                  setDraggingDocumentId(d.id);
                                  setFolderDropError(null);
                                }}
                                onDragEnd={() => setDraggingDocumentId(null)}
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
                      </div>
                      {!triage && (
                        <WorkspaceInspector
                          doc={previewDoc}
                          onOpen={(id) => setSelectedId(id)}
                          onOpenInbox={() => setView("inbox")}
                          onOpenEvidence={() => setView("evidence")}
                        />
                      )}
                    </div>
                  </>
                )}
              </section>
            </>
          )}
          </Suspense>
        </div>
      </div>

      {navOpen && (
        <div className="nav-backdrop" onClick={() => setNavOpen(false)} />
      )}
        <CommandPalette
        open={commandOpen}
        onOpenChange={setCommandOpen}
        canWrite={!!me?.can_write}
        isAdmin={!!me?.is_dms_admin}
        savedViews={savedViews}
        onNavigate={navigateFromCommand}
        onApplyPreset={applyPresetFromCommand}
        onApplySavedView={applySavedView}
        onOpenDocument={openDocumentFromCommand}
      />
    </div>
  );
}

function BulkActionBar({
  selectedCount,
  correspondents,
  documentTypes,
  folders,
  tags,
  folder,
  documentType,
  correspondent,
  reviewStatus,
  addTag,
  removeTag,
  busy,
  error,
  message,
  onFolderChange,
  onDocumentTypeChange,
  onCorrespondentChange,
  onReviewStatusChange,
  onAddTagChange,
  onRemoveTagChange,
  onSelectPage,
  onClear,
  onApply,
  onClassify,
}: {
  selectedCount: number;
  correspondents: NamedRef[];
  documentTypes: NamedRef[];
  folders: FolderRef[];
  tags: TagRef[];
  folder: string;
  documentType: string;
  correspondent: string;
  reviewStatus: string;
  addTag: string;
  removeTag: string;
  busy: boolean;
  error: string | null;
  message: string | null;
  onFolderChange: (value: string) => void;
  onDocumentTypeChange: (value: string) => void;
  onCorrespondentChange: (value: string) => void;
  onReviewStatusChange: (value: string) => void;
  onAddTagChange: (value: string) => void;
  onRemoveTagChange: (value: string) => void;
  onSelectPage: () => void;
  onClear: () => void;
  onApply: () => void;
  onClassify: () => void;
}) {
  return (
    <section className="bulk-bar" aria-label="Massenaktionen">
      <div className="bulk-bar__head">
        <strong>
          {selectedCount} Dokument{selectedCount === 1 ? "" : "e"} ausgewählt
        </strong>
        <div className="bulk-bar__actions">
          <button className="link" onClick={onSelectPage} disabled={busy}>
            Seite auswählen
          </button>
          <button className="link" onClick={onClear} disabled={busy}>
            Auswahl aufheben
          </button>
        </div>
      </div>

      <div className="bulk-bar__grid">
        <label>
          Ordner
          <select value={folder} onChange={(e) => onFolderChange(e.target.value)}>
            <option value="">Nicht ändern</option>
            <option value="__none">Ohne Ordner</option>
            {folders.map((item) => (
              <option key={item.id} value={item.id}>
                {item.full_path}
              </option>
            ))}
          </select>
        </label>
        <label>
          Typ
          <select
            value={documentType}
            onChange={(e) => onDocumentTypeChange(e.target.value)}
          >
            <option value="">Nicht ändern</option>
            <option value="__none">Leeren</option>
            {documentTypes.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Korrespondent
          <select
            value={correspondent}
            onChange={(e) => onCorrespondentChange(e.target.value)}
          >
            <option value="">Nicht ändern</option>
            <option value="__none">Leeren</option>
            {correspondents.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Review
          <select
            value={reviewStatus}
            onChange={(e) => onReviewStatusChange(e.target.value)}
          >
            <option value="">Nicht ändern</option>
            <option value="needs_review">Needs Review</option>
            <option value="reviewed">Reviewed</option>
          </select>
        </label>
        <label>
          Tag hinzufügen
          <select value={addTag} onChange={(e) => onAddTagChange(e.target.value)}>
            <option value="">Keinen</option>
            {tags.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Tag entfernen
          <select
            value={removeTag}
            onChange={(e) => onRemoveTagChange(e.target.value)}
          >
            <option value="">Keinen</option>
            {tags.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <p className="status status--error">{error}</p>}
      {message && <p className="status status--ok">{message}</p>}
      <div className="bulk-bar__footer">
        <button onClick={onApply} disabled={busy}>
          {busy ? "Wende an …" : "Änderungen anwenden"}
        </button>
        <button className="secondary" onClick={onClassify} disabled={busy}>
          Regeln anwenden
        </button>
      </div>
    </section>
  );
}

// Persistente linke Navigation (paperless-like). Auf schmalen Screens als
// Overlay über `open` gesteuert; Aktiv-Zustand über `view`. Unter der Haupt-
// navigation zeigen ausklappbare Stammdaten-Abschnitte (Korrespondenten, Tags,
// Dokumenttypen, Speicherpfade) klickbare Filterlisten (STOAA-50).
function Sidebar({
  view,
  onNavigate,
  username,
  onLogout,
  isAdmin,
  open,
  onClose,
  collapsed,
  onToggleCollapse,
  correspondents,
  tags,
  documentTypes,
  storagePaths,
  folders,
  correspondent,
  tag,
  documentType,
  storagePath,
  folder,
  processingState,
  onCorrespondentChange,
  onTagChange,
  onDocumentTypeChange,
  onStoragePathChange,
  onFolderChange,
  draggingDocumentId,
  folderDropBusy,
  folderDropError,
  onDocumentDropToFolder,
  onProcessingStateChange,
  storagePathEnabled,
  currencyFields,
  currencyFilters,
  onCurrencyChange,
  savedViews,
  activeSavedViewId,
  savedViewsBusy,
  savedViewsError,
  onSavedViewSelect,
  onSavedViewDefault,
  onSavedViewDelete,
}: {
  view: MainView;
  onNavigate: (v: MainView) => void;
  username?: string;
  onLogout: () => void;
  isAdmin: boolean;
  open: boolean;
  onClose: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
  correspondents: NamedRef[];
  tags: TagRef[];
  documentTypes: NamedRef[];
  storagePaths: NamedRef[];
  folders: FolderRef[];
  correspondent: number | "";
  tag: number | "";
  documentType: number | "";
  storagePath: number | "";
  folder: FolderFilterValue;
  processingState: ProcessingStateFilter | "";
  onCorrespondentChange: (v: number | "") => void;
  onTagChange: (v: number | "") => void;
  onDocumentTypeChange: (v: number | "") => void;
  onStoragePathChange: (v: number | "") => void;
  onFolderChange: (v: FolderFilterValue) => void;
  draggingDocumentId: number | null;
  folderDropBusy: number | null;
  folderDropError: string | null;
  onDocumentDropToFolder: (documentId: number, folderId: number | null) => void;
  onProcessingStateChange: (v: ProcessingStateFilter | "") => void;
  storagePathEnabled: boolean;
  currencyFields: CustomField[];
  currencyFilters: Record<number, CurrencyRange>;
  onCurrencyChange: (
    fieldId: number,
    bound: keyof CurrencyRange,
    v: string,
  ) => void;
  savedViews: SavedView[];
  activeSavedViewId: number | null;
  savedViewsBusy: boolean;
  savedViewsError: string | null;
  onSavedViewSelect: (view: SavedView) => void;
  onSavedViewDefault: (view: SavedView) => void;
  onSavedViewDelete: (view: SavedView) => void;
}) {
  // Nach einer Filterauswahl auf Mobil das Overlay schließen (Desktop no-op).
  const pick = (fn: (v: number | "") => void) => (v: number | "") => {
    fn(v);
    onClose();
  };

  return (
    <aside
      className={`sidebar${open ? " sidebar--open" : ""}${collapsed ? " sidebar--collapsed" : ""}`}
    >
      <div className="sidebar__brand">
        <span className="sidebar__logo" title="DMS">
          DMS
        </span>
        {/* Desktop: Icon-only ein-/ausklappen (persistiert). */}
        <button
          className="nav-toggle sidebar__collapse"
          aria-label={collapsed ? "Seitenleiste ausklappen" : "Seitenleiste einklappen"}
          aria-pressed={collapsed}
          onClick={onToggleCollapse}
          title={collapsed ? "Ausklappen" : "Einklappen"}
        >
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path
              fill="currentColor"
              d={
                collapsed
                  ? "M4 4h2v16H4zm5.5 3.5L11 9l-3 3 3 3-1.5 1.5L5 12z"
                  : "M18 4h2v16h-2zm-3.5 3.5L16 9l-3 3 3 3-1.5 1.5L9 12z"
              }
            />
          </svg>
        </button>
        {/* Mobil: Off-Canvas-Drawer schließen. */}
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
      </div>

      <nav className="nav">
        <NavItem
          active={view === "dashboard"}
          onClick={() => onNavigate("dashboard")}
          label="Cockpit"
          icon="M3 4h8v7H3zm10 0h8v4h-8zM3 13h8v7H3zm10-3h8v10h-8z"
        />
        <NavItem
          active={view === "docs"}
          onClick={() => onNavigate("docs")}
          label="Dokumente"
          icon="M6 2h7l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2m7 1.5V8h4.5z"
        />
        <NavItem
          active={view === "copilot"}
          onClick={() => onNavigate("copilot")}
          label="Copilot"
          icon="M12 2a7 7 0 0 1 7 7v1h1a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-1v1a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4v-1H4a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h1V9a7 7 0 0 1 7-7m-3 10a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3m6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3M9 18h6v-2H9z"
        />
        <NavItem
          active={view === "cases"}
          onClick={() => onNavigate("cases")}
          label="Akten"
          icon="M3 5a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v2H3zm0 6h18v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"
        />
        <NavItem
          active={view === "dossiers"}
          onClick={() => onNavigate("dossiers")}
          label="Dossiers"
          icon="M5 3h10l4 4v14H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2m9 1.5V8h3.5M7 11h8v2H7zm0 4h8v2H7z"
        />
        <NavItem
          active={view === "contracts"}
          onClick={() => onNavigate("contracts")}
          label="Verträge"
          icon="M7 2h10a2 2 0 0 1 2 2v16l-4-2-3 2-3-2-4 2V4a2 2 0 0 1 2-2m2 5v2h6V7zm0 4v2h6v-2zm0 4v2h4v-2z"
        />
        <NavItem
          active={view === "knowledge"}
          onClick={() => onNavigate("knowledge")}
          label="Gedächtnis"
          icon="M12 2a4 4 0 0 1 4 4v1h2a3 3 0 0 1 0 6h-2v2h2a3 3 0 1 1 0 6h-4v-4h-4v4H6a3 3 0 1 1 0-6h2v-2H6a3 3 0 0 1 0-6h2V6a4 4 0 0 1 4-4m-2 5h4V6a2 2 0 1 0-4 0zm0 2v2h4V9zm0 4v2h4v-2z"
        />
        <NavItem
          active={view === "inbox"}
          onClick={() => onNavigate("inbox")}
          label="Inbox"
          icon="M4 4h16v14H7l-3 3V4m4 5h8V7H8zm0 4h5v-2H8z"
        />
        {/* Mobil-Erfassung (STOAA-514): Kamera-Foto → PDF → DMS. Prominent
            direkt unter „Dokumente", damit es am Handy schnell erreichbar ist. */}
        <NavItem
          active={view === "capture"}
          onClick={() => onNavigate("capture")}
          label="Erfassen"
          icon="M9 3 7.2 5H4a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3.2L15 3zm3 5a5 5 0 1 1 0 10 5 5 0 0 1 0-10m0 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6"
        />
        <NavItem
          active={view === "faellig"}
          onClick={() => onNavigate("faellig")}
          label="Fristen"
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
        <NavItem
          active={view === "evidence"}
          onClick={() => onNavigate("evidence")}
          label="Beweise"
          icon="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5zm-1 13.2-3.4-3.4L9 10.4l2 2 4.4-4.4L16.8 9z"
        />
        <NavItem
          active={view === "quality"}
          onClick={() => onNavigate("quality")}
          label="Qualität"
          icon="M12 2 3 6v6c0 5 3.8 9.4 9 10 5.2-.6 9-5 9-10V6zm-4 8h2v6H8zm3-3h2v9h-2zm3 5h2v4h-2z"
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
            label="E-Mail"
            icon="M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2m0 2v.5l8 5 8-5V6H4m0 2.8V18h16V8.8l-8 5z"
          />
        )}
        {isAdmin && (
          <NavItem
            active={view === "system"}
            onClick={() => onNavigate("system")}
            label="System"
            icon="M12 2 3 6v6c0 5 3.8 9.4 9 10 5.2-.6 9-5 9-10V6zm-1 5h2v6h-2zm0 8h2v2h-2z"
          />
        )}
      </nav>

      {/* Filter-Bereich als eigenständige, scrollbare Region unterhalb der
          schlanken Primär-Nav. Im Icon-only-Modus per CSS ausgeblendet (nur
          Desktop – der Mobil-Drawer zeigt die Filter stets vollständig). */}
      {view === "docs" && (
        <div className="nav-filters">
          <SavedViewsSection
            views={savedViews}
            activeId={activeSavedViewId}
            busy={savedViewsBusy}
            error={savedViewsError}
            onSelect={onSavedViewSelect}
            onSetDefault={onSavedViewDefault}
            onDelete={onSavedViewDelete}
          />
          <ProcessingFilterSection
            active={processingState}
            onSelect={(v) => {
              onProcessingStateChange(v);
              onClose();
            }}
          />
          <FolderSection
            folders={folders}
            active={folder}
            draggingDocumentId={draggingDocumentId}
            folderDropBusy={folderDropBusy}
            error={folderDropError}
            onSelect={(v) => {
              onFolderChange(v);
              onClose();
            }}
            onDropDocument={onDocumentDropToFolder}
          />
          <FilterSection
            title="Korrespondenten"
            items={correspondents}
            activeId={correspondent}
            onSelect={pick(onCorrespondentChange)}
          />
          <FilterSection
            title="Tags"
            items={tags}
            activeId={tag}
            onSelect={pick(onTagChange)}
            colored
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

// Wie viele Einträge je Sektion sofort sichtbar sind, bevor „mehr anzeigen“
// den Rest einblendet; ab dieser Länge erscheint zudem ein kleines Suchfeld.
const SECTION_TOP_N = 8;
const SECTION_SEARCH_THRESHOLD = 10;

function SavedViewsSection({
  views,
  activeId,
  busy,
  error,
  onSelect,
  onSetDefault,
  onDelete,
}: {
  views: SavedView[];
  activeId: number | null;
  busy: boolean;
  error: string | null;
  onSelect: (view: SavedView) => void;
  onSetDefault: (view: SavedView) => void;
  onDelete: (view: SavedView) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  return (
    <div className="nav-section saved-views">
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
        <span className="nav-section__title">Ansichten</span>
        <span className="nav-section__count">{views.length}</span>
      </button>
      {expanded && (
        <>
          <ul className="nav-section__list saved-views__list">
            {views.map((view) => {
              const active = activeId === view.id;
              return (
                <li key={view.id} className="saved-view-row">
                  <button
                    className={`nav-filter saved-view-row__main${active ? " nav-filter--active" : ""}`}
                    onClick={() => onSelect(view)}
                    aria-current={active ? "true" : undefined}
                    title={view.name}
                  >
                    <span className="nav-filter__label">{view.name}</span>
                    <span className="nav-section__count">{view.count}</span>
                  </button>
                  <button
                    type="button"
                    className={`saved-view-action${view.is_default ? " saved-view-action--active" : ""}`}
                    onClick={() => onSetDefault(view)}
                    disabled={busy}
                    aria-label={`${view.name} als Startansicht markieren`}
                    title={view.is_default ? "Startansicht entfernen" : "Als Startansicht"}
                  >
                    ★
                  </button>
                  <button
                    type="button"
                    className="saved-view-action saved-view-action--danger"
                    onClick={() => onDelete(view)}
                    disabled={busy}
                    aria-label={`${view.name} löschen`}
                    title="Löschen"
                  >
                    ×
                  </button>
                </li>
              );
            })}
            {views.length === 0 && (
              <li className="nav-section__empty muted">
                Keine gespeicherten Ansichten
              </li>
            )}
          </ul>
          {error && <p className="nav-section__error">{error}</p>}
        </>
      )}
    </div>
  );
}

function FolderSection({
  folders,
  active,
  draggingDocumentId,
  folderDropBusy,
  error,
  onSelect,
  onDropDocument,
}: {
  folders: FolderRef[];
  active: FolderFilterValue;
  draggingDocumentId: number | null;
  folderDropBusy: number | null;
  error: string | null;
  onSelect: (v: FolderFilterValue) => void;
  onDropDocument: (documentId: number, folderId: number | null) => void;
}) {
  const [expanded, setExpanded] = useState(active !== "");
  const [dropTarget, setDropTarget] = useState<FolderFilterValue | null>(null);
  const items = [
    { id: "none" as const, label: "Ohne Ordner", count: 0, folderId: null },
    ...folders.map((folder) => ({
      id: folder.id,
      label: folder.full_path,
      count: folder.document_count,
      folderId: folder.id,
    })),
  ];

  useEffect(() => {
    if (draggingDocumentId != null) {
      setExpanded(true);
      return;
    }
    setDropTarget(null);
  }, [draggingDocumentId]);

  function handleDragOver(event: DragEvent<HTMLButtonElement>, itemId: FolderFilterValue) {
    if (draggingDocumentId == null) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    setDropTarget(itemId);
  }

  function handleDrop(
    event: DragEvent<HTMLButtonElement>,
    item: (typeof items)[number],
  ) {
    event.preventDefault();
    setDropTarget(null);
    const raw =
      event.dataTransfer.getData("application/x-dms-document-id") ||
      (draggingDocumentId != null ? String(draggingDocumentId) : "");
    const documentId = Number(raw);
    if (!Number.isInteger(documentId)) return;
    onDropDocument(documentId, item.folderId);
  }

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
        <span className="nav-section__title">Ordner</span>
        <span className="nav-section__count">{folders.length}</span>
      </button>
      {expanded && (
        <ul className="nav-section__list">
          {items.map((item) => {
            const isActive = active === item.id;
            const isDropTarget = dropTarget === item.id;
            return (
              <li key={String(item.id)}>
                <button
                  className={[
                    "nav-filter",
                    isActive ? "nav-filter--active" : "",
                    draggingDocumentId != null ? "nav-filter--drop-enabled" : "",
                    isDropTarget ? "nav-filter--drop-target" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  onClick={() => onSelect(isActive ? "" : item.id)}
                  onDragOver={(event) => handleDragOver(event, item.id)}
                  onDragLeave={() => setDropTarget(null)}
                  onDrop={(event) => handleDrop(event, item)}
                  aria-current={isActive ? "true" : undefined}
                  aria-busy={folderDropBusy != null ? "true" : undefined}
                  title={item.label}
                >
                  <span className="nav-filter__label">{item.label}</span>
                  {typeof item.id === "number" && (
                    <span className="nav-section__count">{item.count}</span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {error && <p className="nav-section__error">{error}</p>}
    </div>
  );
}

// Ausklappbarer Stammdaten-Abschnitt der Sidebar: Titel + Anzahl, darunter eine
// Liste klickbarer Filter. Klick auf den aktiven Eintrag hebt den Filter wieder
// auf. Standardmäßig eingeklappt (öffnet automatisch, wenn ein Filter aktiv ist).
// Lange Listen bekommen ein Suchfeld sowie Top-N + „mehr anzeigen“. `disabled`
// graut den Abschnitt aus (z. B. Speicherpfade, solange der Backend-Filter
// fehlt). Leere Listen werden ausgeblendet.
function FilterSection({
  title,
  items,
  activeId,
  onSelect,
  colored,
  disabled,
  note,
}: {
  title: string;
  items: (NamedRef & { color?: string })[];
  activeId: number | "";
  onSelect: (v: number | "") => void;
  colored?: boolean;
  disabled?: boolean;
  note?: string;
}) {
  // Eingeklappt als Standard; ist bereits ein Filter dieser Sektion aktiv,
  // startet sie offen, damit keine aktive Auswahl verborgen bleibt.
  const [expanded, setExpanded] = useState(activeId !== "");
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);
  if (items.length === 0) return null;

  const q = query.trim().toLowerCase();
  const matches = q
    ? items.filter((it) => it.name.toLowerCase().includes(q))
    : items;
  const visible = showAll ? matches : matches.slice(0, SECTION_TOP_N);
  const hiddenCount = matches.length - visible.length;
  const showSearch = items.length >= SECTION_SEARCH_THRESHOLD;

  return (
    <div className={`nav-section${disabled ? " nav-section--disabled" : ""}`}>
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
        <span className="nav-section__title">{title}</span>
        {note ? (
          <span className="nav-section__note">{note}</span>
        ) : (
          <span className="nav-section__count">{items.length}</span>
        )}
      </button>
      {expanded && (
        <>
          {showSearch && !disabled && (
            <input
              className="nav-search"
              type="search"
              placeholder={`${title} filtern …`}
              aria-label={`${title} filtern`}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setShowAll(false);
              }}
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
            {matches.length === 0 && (
              <li className="nav-section__empty muted">Keine Treffer</li>
            )}
          </ul>
          {hiddenCount > 0 && (
            <button className="nav-more" onClick={() => setShowAll(true)}>
              mehr anzeigen ({hiddenCount})
            </button>
          )}
          {showAll && matches.length > SECTION_TOP_N && (
            <button className="nav-more" onClick={() => setShowAll(false)}>
              weniger anzeigen
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
  // Standardmäßig eingeklappt; offen, falls bereits ein Status-Filter aktiv ist.
  const [expanded, setExpanded] = useState(active !== "");

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
  // Standardmäßig eingeklappt; offen, wenn bereits ein Betrag-Filter gesetzt ist.
  const hasActive = Object.values(filters).some(
    (r) => (r?.gte ?? "") !== "" || (r?.lte ?? "") !== "",
  );
  const [expanded, setExpanded] = useState(hasActive);
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
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
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

function WorkspaceToolbar({
  count,
  selectedCount,
  loading,
  activePreset,
  activeSavedViewName,
  mode,
  hasFilters,
  saveViewBusy,
  onPreset,
  onModeChange,
  onReset,
  onSaveView,
}: {
  count: number;
  selectedCount: number;
  loading: boolean;
  activePreset: WorkspacePreset | "custom";
  activeSavedViewName: string;
  mode: WorkspaceMode;
  hasFilters: boolean;
  saveViewBusy: boolean;
  onPreset: (preset: WorkspacePreset) => void;
  onModeChange: (mode: WorkspaceMode) => void;
  onReset: () => void;
  onSaveView: () => void;
}) {
  const presets: { id: WorkspacePreset; label: string; hint: string }[] = [
    { id: "latest", label: "Neueste", hint: "Aktuelle Dokumente" },
    { id: "processing", label: "In Arbeit", hint: "Pipeline läuft" },
    { id: "failed", label: "Fehler", hint: "Retry nötig" },
    { id: "unfiled", label: "Ohne Ordner", hint: "Einordnen" },
    { id: "inbox", label: "Inbox", hint: "Review" },
    { id: "quality", label: "Qualität", hint: "Mängel" },
  ];
  return (
    <div className="workspace-toolbar">
      <div className="workspace-toolbar__summary">
        <span className="workspace-toolbar__eyebrow">Workspace</span>
        <strong>
          {loading ? "Lade Dokumente" : `${count} Dokument${count === 1 ? "" : "e"}`}
        </strong>
        <span>
          {selectedCount > 0
            ? `${selectedCount} ausgewählt`
            : activeSavedViewName
              ? `Ansicht: ${activeSavedViewName}`
            : activePreset === "custom"
              ? "Eigene Filteransicht"
              : "Schnellansicht aktiv"}
        </span>
      </div>
      <div className="workspace-views" aria-label="Schnellansichten">
        {presets.map((preset) => (
          <button
            key={preset.id}
            type="button"
            className={`workspace-view${activePreset === preset.id ? " workspace-view--active" : ""}`}
            onClick={() => onPreset(preset.id)}
            title={preset.hint}
          >
            <strong>{preset.label}</strong>
            <span>{preset.hint}</span>
          </button>
        ))}
      </div>
      <div className="workspace-toolbar__actions">
        <div className="segmented" aria-label="Darstellung">
          <button
            type="button"
            className={mode === "cards" ? "segmented__item segmented__item--active" : "segmented__item"}
            onClick={() => onModeChange("cards")}
          >
            Karten
          </button>
          <button
            type="button"
            className={mode === "compact" ? "segmented__item segmented__item--active" : "segmented__item"}
            onClick={() => onModeChange("compact")}
          >
            Kompakt
          </button>
        </div>
        <button type="button" className="secondary" onClick={onSaveView} disabled={saveViewBusy}>
          {saveViewBusy ? "Speichere …" : "Ansicht speichern"}
        </button>
        {hasFilters && (
          <button type="button" className="link" onClick={onReset}>
            Zurücksetzen
          </button>
        )}
      </div>
    </div>
  );
}

function WorkspaceInspector({
  doc,
  onOpen,
  onOpenInbox,
  onOpenEvidence,
}: {
  doc: DocumentItem | null;
  onOpen: (id: number) => void;
  onOpenInbox: () => void;
  onOpenEvidence: () => void;
}) {
  const [thumb, setThumb] = useState<string | null>(null);

  useEffect(() => {
    if (!doc) {
      setThumb(null);
      return;
    }
    let url: string | null = null;
    let active = true;
    setThumb(null);
    getDocumentThumbnail(doc.id)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setThumb(url);
      })
      .catch(() => {
        /* Thumbnail optional – der Inspector bleibt mit Metadaten nutzbar. */
      });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [doc]);

  if (!doc) {
    return (
      <aside className="workspace-inspector workspace-inspector--empty">
        <strong>Kein Dokument ausgewählt</strong>
        <span>Wähle eine Karte aus, um Vorschau, Status und Metadaten hier zu sehen.</span>
      </aside>
    );
  }

  const date = doc.created_at || doc.added_at;
  return (
    <aside className="workspace-inspector">
      <div className="workspace-inspector__preview">
        {thumb ? (
          <img src={thumb} alt="" />
        ) : (
          <svg viewBox="0 0 24 24" width="58" height="58" aria-hidden="true">
            <path
              fill="currentColor"
              d="M6 2h7l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2m7 1.5V8h4.5z"
            />
          </svg>
        )}
      </div>
      <div className="workspace-inspector__head">
        <span className="workspace-inspector__asn">
          {doc.id ? `#${doc.id}` : "Dokument"}
        </span>
        <h3>{doc.title}</h3>
        <p>
          {doc.correspondent_name || "Unbekannter Korrespondent"}
          {doc.document_type_name ? ` · ${doc.document_type_name}` : ""}
        </p>
      </div>
      <div className="workspace-inspector__actions">
        <button type="button" onClick={() => onOpen(doc.id)}>
          Öffnen
        </button>
        <button type="button" className="secondary" onClick={onOpenInbox}>
          Inbox
        </button>
        <button type="button" className="secondary" onClick={onOpenEvidence}>
          Beweise
        </button>
      </div>
      <dl className="workspace-inspector__grid">
        <div>
          <dt>Status</dt>
          <dd><ProcessingBadge state={doc.processing_state} /></dd>
        </div>
        <div>
          <dt>Review</dt>
          <dd>{doc.review_status === "reviewed" ? "Geprüft" : "Zu prüfen"}</dd>
        </div>
        <div>
          <dt>Datum</dt>
          <dd>{new Date(date).toLocaleDateString("de-DE")}</dd>
        </div>
        <div>
          <dt>Seiten</dt>
          <dd>{doc.page_count ?? "-"}</dd>
        </div>
        <div>
          <dt>Ordner</dt>
          <dd>{doc.folder_path || "Ohne Ordner"}</dd>
        </div>
        <div>
          <dt>Akte</dt>
          <dd>{doc.case_file_title || "-"}</dd>
        </div>
        <div>
          <dt>Archiv</dt>
          <dd>{doc.archive_status_label}</dd>
        </div>
        <div>
          <dt>OCR</dt>
          <dd>{doc.ocr_status || "-"}</dd>
        </div>
      </dl>
      {doc.tags.length > 0 && (
        <div className="workspace-inspector__tags">
          {doc.tags.map((tagItem) => (
            <span
              key={tagItem.id}
              className="tag"
              style={{ borderColor: tagItem.color, color: tagItem.color }}
            >
              {tagItem.name}
            </span>
          ))}
        </div>
      )}
      {doc.review_task_count > 0 && (
        <div className="workspace-inspector__notice">
          <strong>{doc.review_task_count} offene Aufgabe(n)</strong>
          <span>Dieses Dokument braucht noch menschliche Prüfung.</span>
        </div>
      )}
    </aside>
  );
}

function DocumentCard({
  doc,
  selected,
  previewed,
  mode,
  onPreview,
  onOpen,
  onSelectedChange,
  onDragStart,
  onDragEnd,
}: {
  doc: DocumentItem;
  selected: boolean;
  previewed: boolean;
  mode: WorkspaceMode;
  onPreview: () => void;
  onOpen: () => void;
  onSelectedChange: (checked: boolean) => void;
  onDragStart: () => void;
  onDragEnd: () => void;
}) {
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
    // a11y (#10): Die Karte ist ein Container (KEIN role="button") – ein Button
    // darf keine interaktiven Kinder (Checkbox, „Öffnen") enthalten. Die
    // Primäraktion „Vorschau" trägt der echte Titel-Button unten (tastaturfähig);
    // der onClick hier bleibt reine Maus-Bequemlichkeit für Klicks auf leere
    // Kartenflächen (interaktive Kinder stoppen die Propagation).
    <article
      className={`doc-card doc-card--${mode}${selected ? " doc-card--selected" : ""}${previewed ? " doc-card--previewed" : ""}`}
      draggable
      onClick={onPreview}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("application/x-dms-document-id", String(doc.id));
        event.dataTransfer.setData("text/plain", doc.title);
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      title={`${doc.title} ziehen, um es in einen Ordner zu verschieben`}
    >
      <label
        className="doc-card__select"
        title="Dokument auswählen"
        onClick={(event) => event.stopPropagation()}
      >
        <input
          type="checkbox"
          checked={selected}
          aria-label={`${doc.title} auswählen`}
          onChange={(event) => onSelectedChange(event.target.checked)}
        />
      </label>
      <div className="doc-card__open">
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
          <h3 className="doc-card__title">
            {/* Echter, tastaturfokussierbarer Vorschau-Button (a11y #10). */}
            <button
              type="button"
              className="doc-card__title-btn"
              onClick={(event) => {
                event.stopPropagation();
                onPreview();
              }}
            >
              {doc.title}
            </button>
          </h3>
          <p className="doc-card__meta">
            {doc.correspondent_name ?? "Unbekannt"}
            {doc.document_type_name ? ` · ${doc.document_type_name}` : ""}
            {doc.folder_path ? ` · ${doc.folder_path}` : ""}
            {doc.case_file_title ? ` · Akte: ${doc.case_file_title}` : ""}
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
          <button
            type="button"
            className="doc-card__detail-btn"
            onClick={(event) => {
              event.stopPropagation();
              onOpen();
            }}
          >
            Öffnen
          </button>
        </div>
      </div>
    </article>
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
