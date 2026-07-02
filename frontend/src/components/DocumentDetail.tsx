import { useEffect, useState } from "react";
import {
  getDocument,
  getDocumentPreview,
  updateDocument,
  type DocumentDetail as Detail,
  type NamedRef,
} from "../api";

interface Props {
  id: number;
  onBack: () => void;
  correspondents: NamedRef[];
  documentTypes: NamedRef[];
  allTags: NamedRef[];
  canEdit: boolean;
}

export default function DocumentDetail({
  id,
  onBack,
  correspondents,
  documentTypes,
  allTags,
  canEdit,
}: Props) {
  const [doc, setDoc] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);

  // Bearbeiten-Modus
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [form, setForm] = useState({
    title: "",
    correspondent: "" as number | "",
    document_type: "" as number | "",
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

  const versions = doc?.versions ?? [];
  const version =
    versions.find((v) => v.id === doc?.current_version) ?? versions[versions.length - 1];

  return (
    <div className="shell">
      <header className="topbar">
        <button className="link" onClick={onBack}>
          ← Zurück zur Liste
        </button>
        {doc && canEdit && !editing && (
          <button onClick={startEdit}>Bearbeiten</button>
        )}
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
                <label>
                  Korrespondent
                  <select
                    value={form.correspondent}
                    onChange={(e) =>
                      setForm((f) => ({
                        ...f,
                        correspondent: e.target.value ? Number(e.target.value) : "",
                      }))
                    }
                  >
                    <option value="">— keiner —</option>
                    {correspondents.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Typ
                  <select
                    value={form.document_type}
                    onChange={(e) =>
                      setForm((f) => ({
                        ...f,
                        document_type: e.target.value ? Number(e.target.value) : "",
                      }))
                    }
                  >
                    <option value="">— keiner —</option>
                    {documentTypes.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="edit-tags">
                  <span className="edit-tags__label">Schlagworte</span>
                  <div className="tag-toggle-list">
                    {allTags.length === 0 && (
                      <span className="muted">Noch keine Schlagworte (im Admin anlegen).</span>
                    )}
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
            ) : (
              <>
                <h2>{doc.title}</h2>
                <dl>
                  <dt>Korrespondent</dt>
                  <dd>{doc.correspondent_name ?? "—"}</dd>
                  <dt>Typ</dt>
                  <dd>{doc.document_type_name ?? "—"}</dd>
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
