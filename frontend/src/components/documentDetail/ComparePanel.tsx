import { useState } from "react";
import {
  compareVersions,
  type CompareFieldChange,
  type CompareSectionDiff,
  type DocumentVersion,
  type VersionCompare,
} from "../../api";
import { sanitizeDiffHtml } from "../../sanitize";
import { formatBytes, shortHash } from "./format";

const FIELD_LABELS: Record<string, string> = {
  title: "Titel",
  created_at: "Belegdatum",
  document_type: "Typ",
  correspondent: "Korrespondent",
  storage_path: "Ablagepfad",
  folder: "Ordner",
  case_file: "Akte",
  owner: "Eigentümer",
  status: "Freigabestatus",
  review_status: "Review-Status",
  retention_until: "Aufbewahrung bis",
};

const SECTION_LABELS: Record<string, string> = {
  text: "Text",
  file: "Datei",
  pages: "Seiten",
  metadata: "Metadaten",
  tags: "Tags",
  custom_fields: "Zusatzfelder",
};

function fieldLabel(key: string): string {
  return FIELD_LABELS[key] ?? key;
}

function sectionLabel(key: string): string {
  return SECTION_LABELS[key] ?? key;
}

function formatSizeDelta(delta: number): string {
  if (delta === 0) return "0 B";
  const sign = delta > 0 ? "+" : "-";
  return `${sign}${formatBytes(Math.abs(delta))}`;
}

// CSS-Klasse für eine Zeile eines unified-diff (Backend liefert difflib-Output).
function diffLineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) {
    return "compare-diff__line compare-diff__line--meta";
  }
  if (line.startsWith("+")) return "compare-diff__line compare-diff__line--add";
  if (line.startsWith("-")) return "compare-diff__line compare-diff__line--del";
  return "compare-diff__line";
}

// Ein Summary-Badge: grün = unverändert, akzent = geändert.
function CompareBadge({ label, changed }: { label: string; changed: boolean }) {
  return (
    <span
      className={`compare-badge ${
        changed ? "compare-badge--changed" : "compare-badge--same"
      }`}
    >
      {label}: {changed ? "geändert" : "unverändert"}
    </span>
  );
}

// Eine Änderungszeile alt → neu (für Metadaten/Zusatzfelder, Stufe 2).
function ChangeRow({ label, change }: { label: string; change: CompareFieldChange }) {
  return (
    <div className="compare-change">
      <span className="compare-change__label">{label}</span>
      <span className="compare-change__old">{change.old ?? "—"}</span>
      <span className="compare-change__arrow">→</span>
      <span className="compare-change__new">{change.new ?? "—"}</span>
    </div>
  );
}

// Rendert einen ``{added, removed, changed}``-Sektions-Diff (Metadaten bzw.
// Zusatzfelder, Stufe 2 / STOAA-312). ``added`` wird als „— → Wert", ``removed``
// als „Wert → —" dargestellt, ``changed`` mit den echten alt/neu-Werten – alles
// über die bestehende ChangeRow. Leere Sektion → dezenter Hinweis.
function SectionDiff({ diff }: { diff: CompareSectionDiff }) {
  const changed = Object.entries(diff.changed ?? {});
  const added = Object.entries(diff.added ?? {});
  const removed = Object.entries(diff.removed ?? {});

  if (!changed.length && !added.length && !removed.length) {
    return <p className="muted">Keine Änderungen.</p>;
  }

  return (
    <div className="compare-changes">
      {changed.map(([key, change]) => (
        <ChangeRow key={`c-${key}`} label={fieldLabel(key)} change={change} />
      ))}
      {added.map(([key, value]) => (
        <ChangeRow key={`a-${key}`} label={fieldLabel(key)} change={{ old: null, new: value }} />
      ))}
      {removed.map(([key, value]) => (
        <ChangeRow key={`r-${key}`} label={fieldLabel(key)} change={{ old: value, new: null }} />
      ))}
    </div>
  );
}

// Vergleichsansicht (STOAA-290/313): zwei Versionen wählen und OCR-/Datei-Diff
// anzeigen. Metadaten-/Tag-/Feld-Sektionen werden ab Stufe 2 (STOAA-312) befüllt,
// sobald beide Versionen einen Snapshot tragen (``metadata_versioning_supported``);
// sonst greift weiter der Stufe-1-Hinweis „noch nicht verfügbar".
export function ComparePanel({
  documentId,
  versions,
  onDownload,
}: {
  documentId: number;
  versions: DocumentVersion[];
  onDownload: (versionNo: number) => void;
}) {
  // ``versions`` ist absteigend sortiert (neueste zuerst).
  const newestNo = versions.length ? versions[0].version_no : null;
  const oldestNo = versions.length ? versions[versions.length - 1].version_no : null;

  // Default: älteste (A) vs. neueste (B).
  const [fromNo, setFromNo] = useState<number | null>(oldestNo);
  const [toNo, setToNo] = useState<number | null>(newestNo);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VersionCompare | null>(null);

  async function onCompare() {
    if (fromNo === null || toNo === null) return;
    if (fromNo === toNo) {
      setError("Bitte zwei unterschiedliche Versionen wählen.");
      setResult(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await compareVersions(documentId, fromNo, toNo);
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  if (versions.length < 2) {
    return (
      <div className="version-info compare-panel">
        <h3>Versionsvergleich</h3>
        <p className="muted">
          Für einen Vergleich werden mindestens zwei Versionen benötigt.
        </p>
      </div>
    );
  }

  return (
    <div className="version-info compare-panel">
      <h3>Versionsvergleich</h3>

      <div className="compare-picker">
        <label className="compare-picker__field">
          <span>Version A</span>
          <select
            value={fromNo ?? ""}
            onChange={(e) => setFromNo(Number(e.target.value))}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.version_no}>
                v{v.version_no}
              </option>
            ))}
          </select>
        </label>
        <label className="compare-picker__field">
          <span>Version B</span>
          <select
            value={toNo ?? ""}
            onChange={(e) => setToNo(Number(e.target.value))}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.version_no}>
                v{v.version_no}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={busy || fromNo === null || toNo === null || fromNo === toNo}
          onClick={onCompare}
        >
          {busy ? "Vergleiche …" : "Vergleichen"}
        </button>
      </div>

      {error && <p className="status status--error">{error}</p>}

      {result && <CompareResultView result={result} onDownload={onDownload} />}
    </div>
  );
}

function CompareResultView({
  result,
  onDownload,
}: {
  result: VersionCompare;
  onDownload: (versionNo: number) => void;
}) {
  const { summary, files } = result;
  // Nur wenn beide Versionen einen Metadaten-Snapshot tragen, liefert das Backend
  // echte Metadaten-/Tag-/Feld-Diffs (Stufe 2). Sonst bleibt es beim Stufe-1-
  // Verhalten: „nicht verfügbar"-Hinweis, keine Sektionen.
  const supported = result.metadata_versioning_supported === true;
  // ``text_diff_html`` VOR dem Rendern sanitizen (Team-Vorgabe DOMPurify) – nie
  // ungesäubertes Backend-HTML in dangerouslySetInnerHTML.
  const safeDiffHtml = sanitizeDiffHtml(result.text_diff_html);
  const hasHtml = !!safeDiffHtml;
  const hasText = hasHtml || !!result.text_diff;

  const tagsAdded = result.tags?.added ?? [];
  const tagsRemoved = result.tags?.removed ?? [];
  const sectionsChanged = result.sections_changed ?? [];
  const humanSummary = result.human_summary ?? [];
  const pageSummary = result.page_summary;

  return (
    <div className="compare-result">
      <p className="compare-caption">
        Vergleich v{result.from_version} (A) → v{result.to_version} (B)
      </p>

      <div className="compare-overview">
        <div className="compare-score">
          <strong>{result.change_score}</strong>
          <span>Change Score</span>
        </div>
        <div className="compare-summary">
          <h4>Was hat sich geändert?</h4>
          <ul>
            {humanSummary.map((line, idx) => (
              <li key={`${idx}-${line}`}>{line}</li>
            ))}
          </ul>
          <div className="compare-section-chips">
            {sectionsChanged.length ? (
              sectionsChanged.map((section) => (
                <span key={section}>{sectionLabel(section)}</span>
              ))
            ) : (
              <span>Keine Änderungen</span>
            )}
          </div>
        </div>
      </div>

      {/* Summary-Badges */}
      <div className="compare-badges">
        <CompareBadge label="Text" changed={summary.text_changed} />
        <CompareBadge label="Datei" changed={summary.binary_changed} />
        <CompareBadge label="Seiten" changed={summary.pages_changed} />
        {supported && (
          <>
            <CompareBadge label="Metadaten" changed={summary.metadata_changed} />
            <CompareBadge label="Tags" changed={summary.tags_changed} />
            <CompareBadge
              label="Zusatzfelder"
              changed={summary.custom_fields_changed}
            />
          </>
        )}
      </div>
      {!supported && (
        <p className="muted compare-hint">
          Metadaten-, Tag- und Feld-Vergleich pro Version ist noch nicht
          verfügbar (Stufe 2).
        </p>
      )}

      {/* Datei-/Summary-Sektion */}
      <div className="compare-section">
        <h4>Datei</h4>
        <table className="compare-file-table">
          <thead>
            <tr>
              <th />
              <th>Version A (v{result.from_version})</th>
              <th>Version B (v{result.to_version})</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th>SHA-256</th>
              <td className="mono">{shortHash(files.old_sha256)}</td>
              <td className="mono">{shortHash(files.new_sha256)}</td>
            </tr>
            <tr>
              <th>Hash geändert</th>
              <td colSpan={2}>{files.sha256_changed ? "Ja" : "Nein"}</td>
            </tr>
            <tr>
              <th>Größe</th>
              <td>{formatBytes(files.old_size)}</td>
              <td>{formatBytes(files.new_size)}</td>
            </tr>
            <tr>
              <th>Delta</th>
              <td colSpan={2}>{formatSizeDelta(files.size_delta)}</td>
            </tr>
            <tr>
              <th>MIME</th>
              <td>{files.old_mime_type || "—"}</td>
              <td>{files.new_mime_type || "—"}</td>
            </tr>
            <tr>
              <th>Seiten</th>
              <td>{files.old_page_count ?? "—"}</td>
              <td>{files.new_page_count ?? "—"}</td>
            </tr>
          </tbody>
        </table>
        <div className="compare-downloads">
          <button
            type="button"
            className="link"
            onClick={() => onDownload(result.from_version)}
          >
            Version A herunterladen
          </button>
          <button
            type="button"
            className="link"
            onClick={() => onDownload(result.to_version)}
          >
            Version B herunterladen
          </button>
        </div>
      </div>

      <div className="compare-section">
        <h4>Seiten</h4>
        <div className="compare-page-summary">
          <div>
            <strong>{pageSummary.old_page_count ?? "—"}</strong>
            <span>Version A</span>
          </div>
          <div>
            <strong>{pageSummary.new_page_count ?? "—"}</strong>
            <span>Version B</span>
          </div>
          <div>
            <strong>{pageSummary.added}</strong>
            <span>Hinzugefügt</span>
          </div>
          <div>
            <strong>{pageSummary.removed}</strong>
            <span>Entfernt</span>
          </div>
          <div>
            <strong>{pageSummary.reordered ? "Ja" : "Nein"}</strong>
            <span>Reordered</span>
          </div>
          <div>
            <strong>{pageSummary.rotation_changed ? "Ja" : "Nein"}</strong>
            <span>Rotation geändert</span>
          </div>
        </div>
      </div>

      {/* OCR-Text-Diff */}
      <div className="compare-section">
        <h4>OCR-Textvergleich</h4>
        {!hasText ? (
          <p className="muted">Kein Textunterschied.</p>
        ) : hasHtml ? (
          <div
            className="compare-diff compare-diff--html"
            // Vom Backend erzeugte HtmlDiff-Tabelle (Stufe 2), DOMPurify-sanitized.
            dangerouslySetInnerHTML={{ __html: safeDiffHtml }}
          />
        ) : (
          <pre className="compare-diff compare-diff--text">
            {result.text_diff.split("\n").map((line, i) => (
              <span key={i} className={diffLineClass(line)}>
                {line + "\n"}
              </span>
            ))}
          </pre>
        )}
      </div>

      {/* Metadaten / Tags / Zusatzfelder – nur bei echter Metadaten-Versionierung
          (Stufe 2, STOAA-312). Ohne beidseitigen Snapshot bleibt es beim
          Stufe-1-Hinweis oben; die Sektionen erscheinen dann gar nicht. */}
      {supported && (
        <>
          <div className="compare-section">
            <h4>Metadaten</h4>
            <SectionDiff diff={result.metadata} />
          </div>

          <div className="compare-section">
            <h4>Tags</h4>
            {tagsAdded.length || tagsRemoved.length ? (
              <div className="compare-tags">
                {tagsAdded.map((t) => (
                  <span
                    key={`a-${t.id}`}
                    className="compare-tag compare-tag--added"
                  >
                    + {t.name}
                  </span>
                ))}
                {tagsRemoved.map((t) => (
                  <span
                    key={`r-${t.id}`}
                    className="compare-tag compare-tag--removed"
                  >
                    − {t.name}
                  </span>
                ))}
              </div>
            ) : (
              <p className="muted">Keine Tag-Änderungen.</p>
            )}
          </div>

          <div className="compare-section">
            <h4>Zusatzfelder</h4>
            <SectionDiff diff={result.custom_fields} />
          </div>
        </>
      )}
    </div>
  );
}
