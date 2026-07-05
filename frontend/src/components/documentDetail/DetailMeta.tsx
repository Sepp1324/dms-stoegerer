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
  onRetry,
  onDownloadQr,
}: {
  doc: Detail;
  canEdit: boolean;
  currentVersion: DocumentVersion | undefined;
  retryBusy: boolean;
  retryError: string | null;
  onRetry: () => void;
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
