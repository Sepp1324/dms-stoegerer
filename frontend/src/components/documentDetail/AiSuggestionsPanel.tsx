// KI-Vorschläge-Panel (nur bei Schreibrecht). Aus dem Haupt-Render von
// DocumentDetail.tsx extrahiert (STOAA-431) – Verhalten unverändert. Die
// Vorschlagszeilen werden vom Orchestrator vorberechnet und hier nur angezeigt.
export function AiSuggestionsPanel({
  suggestionRows,
  summary,
  applying,
  regenerating,
  regenNote,
  applyError,
  onRegenerate,
  onApply,
  onDismiss,
}: {
  suggestionRows: { key: string; label: string; value: string }[];
  summary: string | undefined;
  applying: boolean;
  regenerating: boolean;
  regenNote: string | null;
  applyError: string | null;
  onRegenerate: () => void;
  onApply: (fields?: string[]) => void;
  onDismiss: (field: string) => void;
}) {
  return (
    <div className="ai-panel">
      <div className="ai-panel__head">
        <span>
          <i aria-hidden="true">✦</i> KI-Vorschläge
        </span>
        <div className="ai-panel__actions">
          <button
            className="link"
            onClick={onRegenerate}
            disabled={regenerating || applying}
          >
            {regenerating ? "Generiere …" : "Neu generieren"}
          </button>
          {suggestionRows.length > 0 && (
            <button onClick={() => onApply()} disabled={applying || regenerating}>
              {applying ? "…" : "Alle übernehmen"}
            </button>
          )}
        </div>
      </div>
      {summary && (
        <div className="ai-panel__summary-row">
          <p className="ai-panel__summary">{summary}</p>
          <button
            className="link ai-suggestions__dismiss"
            onClick={() => onDismiss("summary")}
            disabled={applying || regenerating}
            title="Zusammenfassung verwerfen"
          >
            Verwerfen
          </button>
        </div>
      )}
      {suggestionRows.length > 0 ? (
        <ul className="ai-suggestions">
          {suggestionRows.map((row) => (
            <li key={row.key}>
              <span className="ai-suggestions__label">{row.label}</span>
              <span className="ai-suggestions__value">{row.value}</span>
              <button
                className="link"
                onClick={() => onApply([row.key])}
                disabled={applying || regenerating}
              >
                Übernehmen
              </button>
              <button
                className="link ai-suggestions__dismiss"
                onClick={() => onDismiss(row.key)}
                disabled={applying || regenerating}
                title={`${row.label} verwerfen`}
              >
                Verwerfen
              </button>
            </li>
          ))}
        </ul>
      ) : (
        !summary && (
          <p className="muted ai-panel__empty">
            Keine KI-Vorschläge vorhanden.
          </p>
        )
      )}
      {regenNote && <p className="status status--warn">{regenNote}</p>}
      {applyError && <p className="status status--error">{applyError}</p>}
    </div>
  );
}
