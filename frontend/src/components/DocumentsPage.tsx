import { useEffect, useMemo, useState } from "react";
import {
  getCorrespondents,
  getDocuments,
  getDocumentTypes,
  getMe,
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
    Promise.all([getCorrespondents(), getDocumentTypes(), getTags()])
      .then(([c, d, t]) => {
        setCorrespondents(c);
        setDocumentTypes(d);
        setTags(t);
      })
      .catch(() => {
        /* Filter sind optional – Fehler hier nicht blockierend */
      });
  }, []);

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
    return <DocumentDetail id={selectedId} onBack={() => setSelectedId(null)} />;
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
              <table className="doc-table">
                <thead>
                  <tr>
                    <th>Titel</th>
                    <th>Korrespondent</th>
                    <th>Typ</th>
                    <th>Seiten</th>
                    <th>Aufgenommen</th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) => (
                    <tr key={d.id} className="doc-row" onClick={() => setSelectedId(d.id)}>
                      <td className="doc-title">{d.title}</td>
                      <td>{d.correspondent_name ?? "—"}</td>
                      <td>{d.document_type_name ?? "—"}</td>
                      <td>{d.page_count ?? "—"}</td>
                      <td>{new Date(d.added_at).toLocaleDateString("de-DE")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </section>
    </div>
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
