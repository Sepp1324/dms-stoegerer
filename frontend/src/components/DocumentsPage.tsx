import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  createCorrespondent,
  createDocumentType,
  createStoragePath,
  createTag,
  getCorrespondents,
  getDocuments,
  getDocumentThumbnail,
  getDocumentTypes,
  getMe,
  getStoragePaths,
  getTags,
  logout,
  type DocumentItem,
  type Me,
  type NamedRef,
} from "../api";
import UploadZone from "./UploadZone";
import DocumentDetail from "./DocumentDetail";
import RulesPage from "./RulesPage";

// Muss dem Backend entsprechen (DRF PageNumberPagination, config/settings.py:
// REST_FRAMEWORK["PAGE_SIZE"] = 25). Nur für die Anzeige „Seite X von N" nötig;
// die Rand-Buttons werden zusätzlich über next/previous der Antwort abgesichert.
const PAGE_SIZE = 25;

export default function DocumentsPage({ onLogout }: { onLogout: () => void }) {
  const [q, setQ] = useState("");
  const [correspondent, setCorrespondent] = useState<number | "">("");
  const [documentType, setDocumentType] = useState<number | "">("");
  const [tag, setTag] = useState<number | "">("");
  // Sortierung; "" = Backend-Standard (FTS-Relevanz bei Suche, sonst Datum neu→alt).
  const [ordering, setOrdering] = useState("");

  const [correspondents, setCorrespondents] = useState<NamedRef[]>([]);
  const [documentTypes, setDocumentTypes] = useState<NamedRef[]>([]);
  const [tags, setTags] = useState<NamedRef[]>([]);
  const [storagePaths, setStoragePaths] = useState<NamedRef[]>([]);

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
  const [view, setView] = useState<"docs" | "rules">("docs");
  // Sidebar auf schmalen Screens ein-/ausklappbar.
  const [navOpen, setNavOpen] = useState(false);

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
  }, []);

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

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    getDocuments({
      q: debouncedQ,
      correspondent,
      document_type: documentType,
      tag,
      ordering,
      page,
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
  }, [debouncedQ, correspondent, documentType, tag, ordering, page, reloadKey]);

  const hasFilters = useMemo(
    () => !!(debouncedQ || correspondent || documentType || tag),
    [debouncedQ, correspondent, documentType, tag],
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
  function onOrderingChange(v: string) {
    setOrdering(v);
    setPage(1);
  }

  function resetFilters() {
    setQ("");
    setCorrespondent("");
    setDocumentType("");
    setTag("");
    setOrdering("");
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
        canEdit={!!me?.can_write}
        onCreateCorrespondent={addCorrespondent}
        onCreateDocumentType={addDocumentType}
        onCreateStoragePath={addStoragePath}
        onCreateTag={addTag}
      />
    );
  }

  const navigate = (v: "docs" | "rules") => {
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
        open={navOpen}
        onClose={() => setNavOpen(false)}
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
            {view === "rules" ? "Regeln" : "Dokumente"}
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

              <section className="filters card">
                <div className="filter-row">
                  <Select
                    label="Korrespondent"
                    value={correspondent}
                    onChange={onCorrespondentChange}
                    options={correspondents}
                  />
                  <Select
                    label="Typ"
                    value={documentType}
                    onChange={onDocumentTypeChange}
                    options={documentTypes}
                  />
                  <Select label="Tag" value={tag} onChange={onTagChange} options={tags} />
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
                      hasFilters
                        ? "Keine Treffer für die aktuellen Filter"
                        : "Noch keine Dokumente"
                    }
                    detail={
                      hasFilters
                        ? "Passe die Suche oder Filter an."
                        : "Lade ein Dokument hoch, um zu beginnen."
                    }
                    action={
                      hasFilters ? (
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
                      {docs.map((d) => (
                        <DocumentCard
                          key={d.id}
                          doc={d}
                          onOpen={() => setSelectedId(d.id)}
                        />
                      ))}
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
// Overlay über `open` gesteuert; Aktiv-Zustand über `view`.
function Sidebar({
  view,
  onNavigate,
  username,
  onLogout,
  open,
  onClose,
}: {
  view: "docs" | "rules";
  onNavigate: (v: "docs" | "rules") => void;
  username?: string;
  onLogout: () => void;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <aside className={`sidebar${open ? " sidebar--open" : ""}`}>
      <div className="sidebar__brand">
        <span className="sidebar__logo">DMS</span>
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
          active={view === "docs"}
          onClick={() => onNavigate("docs")}
          label="Dokumente"
          icon="M6 2h7l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2m7 1.5V8h4.5z"
        />
        <NavItem
          active={view === "rules"}
          onClick={() => onNavigate("rules")}
          label="Regeln"
          icon="M3 5h18v2H3zm0 6h12v2H3zm0 6h18v2H3z"
        />
      </nav>

      <div className="sidebar__footer">
        {username && <span className="muted sidebar__user">{username}</span>}
        <button className="link" onClick={onLogout}>
          Abmelden
        </button>
      </div>
    </aside>
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
    >
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <path fill="currentColor" d={icon} />
      </svg>
      {label}
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
        {doc.tags.length > 0 && (
          <div className="doc-card__tags">
            {doc.tags.map((t) => (
              <span key={t.id} className="tag" style={{ borderColor: t.color, color: t.color }}>
                {t.name}
              </span>
            ))}
          </div>
        )}
        <p className="doc-card__date">
          {new Date(doc.added_at).toLocaleDateString("de-DE")}
        </p>
      </div>
    </button>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: number | "";
  onChange: (v: number | "") => void;
  options: NamedRef[];
}) {
  return (
    <label className="filter">
      <span>{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : "")}
      >
        <option value="">Alle</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    </label>
  );
}
