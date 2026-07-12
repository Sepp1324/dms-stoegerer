import { useEffect, useState } from "react";

import { getDuplicateReport, type DuplicateReport } from "../api";

/**
 * Korpus-Report: listet Paare inhaltlicher Beinah-Duplikate im eigenen Bestand,
 * damit man die Ablage gezielt entrümpeln kann. Jede Seite lässt sich öffnen.
 */
export default function DuplicateReportModal({
  onClose,
  onOpenDocument,
}: {
  onClose: () => void;
  onOpenDocument: (documentId: number) => void;
}) {
  const [data, setData] = useState<DuplicateReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    let active = true;
    getDuplicateReport()
      .then((res) => active && setData(res))
      .catch(() => active && setError("Report konnte nicht geladen werden."))
      .finally(() => active && setBusy(false));
    return () => {
      active = false;
    };
  }, []);

  function open(id: number) {
    onClose();
    onOpenDocument(id);
  }

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Dubletten-Report"
      onClick={onClose}
    >
      <div className="modal-card semantic-search" onClick={(e) => e.stopPropagation()}>
        <div className="modal-card__head">
          <h3>Dubletten im Bestand</h3>
          <button className="link" onClick={onClose} aria-label="Schließen">
            ✕
          </button>
        </div>

        {busy && <p className="muted">Durchsuche den Bestand …</p>}
        {error && <p className="form-error">{error}</p>}
        {data?.status === "disabled" && (
          <p className="muted">Der semantische Index ist deaktiviert.</p>
        )}
        {data?.status === "ok" && data.pairs.length === 0 && (
          <p className="muted">Keine inhaltlichen Dubletten gefunden. 🎉</p>
        )}

        {data?.status === "ok" && data.pairs.length > 0 && (
          <>
            <p className="muted">
              {data.count} mögliche{data.count === 1 ? "s Paar" : " Paare"} gefunden.
            </p>
            <div className="semantic-search__results">
              {data.pairs.map((pair) => (
                <article
                  key={`${pair.a}-${pair.b}`}
                  className="card dup-pair"
                >
                  <div className="dup-pair__head">
                    <span
                      className={`duplicates__badge duplicates__badge--${pair.kind}`}
                    >
                      {pair.kind === "duplicate" ? "Duplikat" : "Mögliche Version"}
                    </span>
                    <span className="dup-pair__score muted">
                      {Math.round(pair.score * 100)} % ähnlich
                    </span>
                  </div>
                  <div className="dup-pair__docs">
                    <button className="link" onClick={() => open(pair.a)}>
                      {pair.a_title}
                    </button>
                    <span className="muted">↔</span>
                    <button className="link" onClick={() => open(pair.b)}>
                      {pair.b_title}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
