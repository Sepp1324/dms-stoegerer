import type { DocumentDetail as Detail, DocumentVersion } from "../../api";
import { ProcessingPanel } from "./ProcessingPanel";

// Anzeige des Übersicht-Tabs (Nur-Lesen): Titel, Verarbeitungs-Widget,
// Klassifizierungshinweis und Metadaten-Liste. Aus dem Haupt-Render von
// DocumentDetail.tsx extrahiert (STOAA-431) – Verhalten unverändert.
export function DetailMeta({
  doc,
  canEdit,
  currentVersion,
  retryBusy,
  retryError,
  archiveBusy,
  archiveError,
  onRetry,
  onArchiveCheck,
  onToggleLegalHold,
  onDownloadRevisionPackage,
  onDownloadQr,
}: {
  doc: Detail;
  canEdit: boolean;
  currentVersion: DocumentVersion | undefined;
  retryBusy: boolean;
  retryError: string | null;
  archiveBusy: boolean;
  archiveError: string | null;
  onRetry: () => void;
  onArchiveCheck: () => void;
  onToggleLegalHold: () => void;
  onDownloadRevisionPackage: () => void;
  onDownloadQr: () => void;
}) {
  return (
    <>
      <h2>{doc.title}</h2>
      <ProcessingPanel
        version={currentVersion}
        canEdit={canEdit}
        retryBusy={retryBusy}
        retryError={retryError}
        onRetry={onRetry}
      />
      {doc.classification?.rules?.length ? (
        <p className="class-note">
          <i aria-hidden="true">⚙</i> Automatisch klassifiziert durch Regel
          {doc.classification.rules.length > 1 ? "n" : ""}{" "}
          „{doc.classification.rules.join("“, „")}“
        </p>
      ) : null}
      <section className={`archive-box archive-box--${doc.archive_status}`}>
        <div>
          <strong>Archiv: {doc.archive_status_label}</strong>
          <span>
            {doc.archive_checked_at
              ? ` geprüft am ${new Date(doc.archive_checked_at).toLocaleString("de-DE")}`
              : " noch nicht geprüft"}
          </span>
        </div>
        <div>
          <strong>{doc.legal_hold ? "Legal Hold aktiv" : "Kein Legal Hold"}</strong>
          <span>
            {doc.legal_hold
              ? doc.legal_hold_reason || "Manuelle Löschsperre"
              : retentionText(doc)}
          </span>
        </div>
        {archiveError && <p className="status status--error">{archiveError}</p>}
        {canEdit && (
          <div className="archive-box__actions">
            <button type="button" onClick={onArchiveCheck} disabled={archiveBusy}>
              {archiveBusy ? "Prüfe …" : "Archiv prüfen"}
            </button>
            <button type="button" onClick={onDownloadRevisionPackage} disabled={archiveBusy}>
              Revisionspaket
            </button>
            <button type="button" className="link" onClick={onToggleLegalHold} disabled={archiveBusy}>
              {doc.legal_hold ? "Legal Hold entfernen" : "Legal Hold setzen"}
            </button>
          </div>
        )}
      </section>
      <dl>
        <dt>Archivnummer</dt>
        <dd className="asn">
          {doc.asn_label ? (
            <>
              <span className="asn__value">{doc.asn_label}</span>
              <button
                type="button"
                className="link"
                onClick={onDownloadQr}
              >
                QR-Code herunterladen
              </button>
            </>
          ) : (
            "—"
          )}
        </dd>
        <dt>Korrespondent</dt>
        <dd>{doc.correspondent_name ?? "—"}</dd>
        <dt>Typ</dt>
        <dd>{doc.document_type_name ?? "—"}</dd>
        <dt>Ordner</dt>
        <dd>{doc.folder_path ?? "—"}</dd>
        <dt>Ablagepfad</dt>
        <dd>{doc.storage_path_name ?? "—"}</dd>
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
    </>
  );
}

function retentionText(doc: Detail): string {
  const state = doc.retention_state;
  if (state.state === "none") return "Keine Aufbewahrungsfrist";
  if (state.state === "expired") return `Aufbewahrung abgelaufen seit ${state.retention_until}`;
  if (state.state === "due_soon") return `Aufbewahrung bald fällig: ${state.retention_until}`;
  if (state.state === "active") return `Aufbewahrung bis ${state.retention_until}`;
  return "Legal Hold";
}
