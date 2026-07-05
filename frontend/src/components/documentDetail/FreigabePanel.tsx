import { useState } from "react";
import type { DocumentStatus } from "../../api";

// Deutsche Labels + Farbakzent (CSS-Modifier) je Freigabe-Status.
const STATUS_LABELS: Record<DocumentStatus, string> = {
  entwurf: "Entwurf",
  zur_freigabe: "Zur Freigabe",
  freigegeben: "Freigegeben",
  abgelehnt: "Abgelehnt",
};

// Statusanzeige + Freigabe-Buttons (Stufe 4). Buttons nur bei Schreibrecht und
// passend zum aktuellen Status; ``freigegeben`` bietet keine Aktion.
export function FreigabePanel({
  status,
  canEdit,
  busy,
  error,
  onSubmit,
  onApprove,
  onReject,
}: {
  status: DocumentStatus;
  canEdit: boolean;
  busy: boolean;
  error: string | null;
  onSubmit: () => void;
  onApprove: () => void;
  onReject: (reason?: string) => void;
}) {
  // Ablehnen mit optionaler Begründung: erst Eingabe einblenden, dann bestätigen.
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  // Unbekannter/fehlender Status (z. B. vor BE-Merge): neutraler Fallback-Text.
  const label = STATUS_LABELS[status] ?? "Unbekannt";
  const accent = STATUS_LABELS[status] ? status : "unknown";

  function confirmReject() {
    onReject(reason.trim() || undefined);
    setRejecting(false);
    setReason("");
  }

  return (
    <div className="freigabe">
      <div className="freigabe__head">
        <span className="freigabe__label">Status</span>
        <span className={`freigabe-badge freigabe-badge--${accent}`}>{label}</span>
      </div>

      {canEdit && (
        <div className="freigabe__actions">
          {(status === "entwurf" || status === "abgelehnt") && (
            <button onClick={onSubmit} disabled={busy}>
              {busy ? "…" : "Zur Freigabe"}
            </button>
          )}
          {status === "zur_freigabe" && !rejecting && (
            <>
              <button onClick={onApprove} disabled={busy}>
                {busy ? "…" : "Genehmigen"}
              </button>
              <button
                className="freigabe__reject"
                onClick={() => setRejecting(true)}
                disabled={busy}
              >
                Ablehnen
              </button>
            </>
          )}
          {status === "zur_freigabe" && rejecting && (
            <div className="freigabe__reject-form">
              <input
                value={reason}
                placeholder="Begründung (optional)"
                onChange={(e) => setReason(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && confirmReject()}
                autoFocus
              />
              <button
                className="freigabe__reject"
                onClick={confirmReject}
                disabled={busy}
              >
                {busy ? "…" : "Ablehnen bestätigen"}
              </button>
              <button
                className="link"
                onClick={() => {
                  setRejecting(false);
                  setReason("");
                }}
                disabled={busy}
              >
                Abbrechen
              </button>
            </div>
          )}
        </div>
      )}

      {error && <p className="status status--error">{error}</p>}
    </div>
  );
}
