import { useEffect, useState } from "react";

import { getFolders, setFolderShared, type FolderRef } from "../api";

/**
 * Ordnerweite Familien-Freigabe: pro Ordner ein Schalter „für die Familie
 * freigeben". Wirkt auf alle Dokumente im Ordner UND seinen Unterordnern (nur
 * Lesen; Eigentümer behält Schreibrechte). Zentral im Familien-Bereich.
 */
export default function FolderSharingPanel() {
  const [folders, setFolders] = useState<FolderRef[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<number | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const list = await getFolders();
      setFolders([...list].sort((a, b) => a.full_path.localeCompare(b.full_path, "de")));
    } catch {
      setError("Ordner konnten nicht geladen werden.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function toggle(folder: FolderRef) {
    setPending(folder.id);
    setError(null);
    try {
      const updated = await setFolderShared(folder.id, !folder.shared_with_household);
      setFolders((prev) => prev.map((f) => (f.id === folder.id ? { ...f, ...updated } : f)));
    } catch {
      setError("Freigabe fehlgeschlagen (Schreibrecht nötig).");
    } finally {
      setPending(null);
    }
  }

  return (
    <section className="card folder-sharing">
      <h3>Ordner für die Familie freigeben</h3>
      <p className="muted folder-sharing__hint">
        Ein freigegebener Ordner macht alle darin (und in seinen Unterordnern)
        liegenden Dokumente für die Haushaltsmitglieder lesbar.
      </p>

      {loading && <p className="muted">Lade Ordner …</p>}
      {error && <p className="form-error">{error}</p>}
      {!loading && folders.length === 0 && (
        <p className="muted">Noch keine Ordner angelegt.</p>
      )}

      {folders.length > 0 && (
        <ul className="folder-sharing__list">
          {folders.map((folder) => (
            <li key={folder.id} className="folder-sharing__row">
              <label className="folder-sharing__label">
                <input
                  type="checkbox"
                  checked={folder.shared_with_household}
                  disabled={pending === folder.id}
                  onChange={() => toggle(folder)}
                />
                <span>{folder.full_path}</span>
              </label>
              <span className="muted folder-sharing__count">{folder.document_count}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
