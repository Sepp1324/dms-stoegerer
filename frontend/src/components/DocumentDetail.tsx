import { useEffect, useState } from "react";
import {
  getDocument,
  getDocumentPreview,
  type DocumentDetail as Detail,
} from "../api";

export default function DocumentDetail({
  id,
  onBack,
}: {
  id: number;
  onBack: () => void;
}) {
  const [doc, setDoc] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getDocument(id)
      .then((d) => active && setDoc(d))
      .catch((e) => active && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
  }, [id]);

  // Vorschau-PDF als Blob laden und als Object-URL einbetten.
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

  const versions = doc?.versions ?? [];
  const version =
    versions.find((v) => v.id === doc?.current_version) ?? versions[versions.length - 1];

  return (
    <div className="shell">
      <header className="topbar">
        <button className="link" onClick={onBack}>
          ← Zurück zur Liste
        </button>
      </header>

      {error && <p className="status status--error">{error}</p>}
      {!doc && !error && <p className="muted">Lade …</p>}

      {doc && (
        <div className="detail">
          <section className="card detail-meta">
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
                      <span key={t.id} className="tag" style={{ borderColor: t.color }}>
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
