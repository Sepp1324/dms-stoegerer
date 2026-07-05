import type { DocumentIntegrity, DocumentVersion } from "../../api";
import { formatBytes } from "./format";

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

export function VersionsPanel({
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
