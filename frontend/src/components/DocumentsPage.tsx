import { useEffect, useMemo, useState } from "react";
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

export default function DocumentsPage({ onLogout }: { onLogout: () => void }) {
  const [q, setQ] = useState("");
  const [correspondent, setCorrespondent] = useState<number | "">("");
  const [documentType, setDocumentType] = useState<number | "">("");
  const [tag, setTag] = useState<number | "">("");

  const [correspondents, setCorrespondents] = useState<NamedRef[]>([]);
  const [documentTypes, setDocumentTypes] = useState<NamedRef[]>([]);
  const [tags, setTags] = useState<NamedRef[]>([]);
  const [storagePaths, setStoragePaths] = useState<NamedRef[]>([]);

  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [me, setMe] = useState<Me | null>(null);
  // Wird nach jedem Upload erhöht → löst ein Neuladen der Liste aus.
  const [reloadKey, setReloadKey] = useState(0);
  // Aktuell geöffnetes Dokument (Detailansicht) oder null (Liste).
  const [selectedId, setSelectedId] = useState<number | null>(null);

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
    })
      .then((page) => {
        if (!active) return;
        setDocs(page.results);
        setCount(page.count);
      })
      .catch((err) => active && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [debouncedQ, correspondent, documentType, tag, reloadKey]);

  const hasFilters = useMemo(
    () => !!(debouncedQ || correspondent || documentType || tag),
    [debouncedQ, correspondent, documentType, tag],
  );

  function resetFilters() {
    setQ("");
    setCorrespondent("");
    setDocumentType("");
    setTag("");
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

  return (
    <div className="shell">
      <header className="topbar">
        <h1>DMS</h1>
        <div className="topbar-right">
          {me && <span className="muted user">{me.username}</span>}
          <button className="link" onClick={handleLogout}>
            Abmelden
          </button>
        </div>
      </header>

      {me?.can_write && (
        <UploadZone onUploaded={() => setReloadKey((k) => k + 1)} />
      )}

      <section className="filters card">
        <input
          className="search"
          placeholder="Volltextsuche (Titel & Inhalt) …"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="filter-row">
          <Select
            label="Korrespondent"
            value={correspondent}
            onChange={setCorrespondent}
            options={correspondents}
          />
          <Select
            label="Typ"
            value={documentType}
            onChange={setDocumentType}
            options={documentTypes}
          />
          <Select label="Tag" value={tag} onChange={setTag} options={tags} />
          {hasFilters && (
            <button className="link" onClick={resetFilters}>
              Zurücksetzen
            </button>
          )}
        </div>
      </section>

      <section>
        {loading && <p className="muted">Lade …</p>}
        {error && <p className="status status--error">{error}</p>}
        {!loading && !error && (
          <>
            <p className="muted result-count">
              {count} {count === 1 ? "Dokument" : "Dokumente"}
            </p>
            {docs.length === 0 ? (
              <p className="muted">Keine Dokumente gefunden.</p>
            ) : (
              <div className="doc-grid">
                {docs.map((d) => (
                  <DocumentCard key={d.id} doc={d} onOpen={() => setSelectedId(d.id)} />
                ))}
              </div>
            )}
          </>
        )}
      </section>
    </div>
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
