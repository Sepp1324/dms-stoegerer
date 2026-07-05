import { useState } from "react";
import type { DocumentVersion } from "../../api";
import {
  ProcessingBadge,
  ocrStatusLabel,
  processingStateLabel,
} from "../ProcessingStatus";

// Verarbeitungs-Widget (STOAA-249): kompaktes Monitoring der aktuellen Version –
// processing_state, OCR-Status, fehlgeschlagener Schritt, letzter Fehlerzeitpunkt
// und Versuche. Fehlerdetails (processing_error/ocr_error) sind aufklappbar; der
// Retry-Button erscheint nur bei ``failed`` und Schreibrecht.
export function ProcessingPanel({
  version,
  canEdit,
  retryBusy,
  retryError,
  onRetry,
}: {
  version: DocumentVersion | undefined;
  canEdit: boolean;
  retryBusy: boolean;
  retryError: string | null;
  onRetry: () => void;
}) {
  const [showErrors, setShowErrors] = useState(false);
  if (!version) return null;

  const state = version.processing_state ?? null;
  const isFailed = state === "failed";
  const hasErrorDetails = !!(version.processing_error || version.ocr_error);

  return (
    <div className="processing">
      <div className="processing__head">
        <span className="processing__label">Verarbeitung</span>
        <ProcessingBadge state={state} />
      </div>

      <dl className="processing__grid">
        <dt>Status</dt>
        <dd>{processingStateLabel(state)}</dd>
        <dt>OCR</dt>
        <dd>{ocrStatusLabel(version.ocr_status ?? null)}</dd>
        {version.processing_failed_step && (
          <>
            <dt>Fehlgeschlagener Schritt</dt>
            <dd>{version.processing_failed_step}</dd>
          </>
        )}
        {version.processing_failed_at && (
          <>
            <dt>Letzter Fehler</dt>
            <dd>{new Date(version.processing_failed_at).toLocaleString("de-DE")}</dd>
          </>
        )}
        <dt>Versuche</dt>
        <dd>{version.processing_attempts}</dd>
      </dl>

      {hasErrorDetails && (
        <div className="processing__errors">
          <button
            className="link processing__toggle"
            onClick={() => setShowErrors((v) => !v)}
            aria-expanded={showErrors}
          >
            {showErrors ? "Fehlerdetails ausblenden" : "Fehlerdetails anzeigen"}
          </button>
          {showErrors && (
            <div className="processing__error-body">
              {version.processing_error && (
                <div>
                  <p className="processing__error-title">Verarbeitungsfehler</p>
                  <pre className="processing__error-text">{version.processing_error}</pre>
                </div>
              )}
              {version.ocr_error && (
                <div>
                  <p className="processing__error-title">OCR-Fehler</p>
                  <pre className="processing__error-text">{version.ocr_error}</pre>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {isFailed && canEdit && (
        <button
          className="processing__retry"
          onClick={onRetry}
          disabled={retryBusy}
        >
          {retryBusy ? "Wird neu gestartet …" : "Verarbeitung erneut starten"}
        </button>
      )}
      {retryError && <p className="status status--error">{retryError}</p>}
    </div>
  );
}
