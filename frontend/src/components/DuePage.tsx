import { useEffect, useState } from "react";
import { getDocument, getDueReminders, type Reminder } from "../api";

// Reines Datum ("YYYY-MM-DD") ohne Zeitzonen-Verschiebung als "DD.MM.YYYY".
function formatDateOnly(date: string): string {
  const m = date.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : date;
}

// Auswahl des Vorschau-Horizonts (Tage) für „anstehende" Erinnerungen.
const HORIZON_CHOICES = [7, 14, 30] as const;

// Topbar-Seite „Wiedervorlage/Fällig" (STOAA-372/374). Zeigt die offenen
// fälligen und anstehenden Erinnerungen des Nutzers (owner-gescopet über das
// Backend) in zwei Gruppen. Ein Klick auf einen Eintrag springt ins Dokument.
export default function DuePage({
  onOpenDocument,
}: {
  onOpenDocument: (documentId: number) => void;
}) {
  const [days, setDays] = useState<number>(7);
  const [faellig, setFaellig] = useState<Reminder[]>([]);
  const [anstehend, setAnstehend] = useState<Reminder[]>([]);
  // Dokument-Titel werden nachgeladen (die Erinnerung trägt nur die Dokument-ID).
  const [titles, setTitles] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    getDueReminders(days)
      .then(async (due) => {
        if (!active) return;
        setFaellig(due.faellig);
        setAnstehend(due.anstehend);
        // Titel für alle referenzierten Dokumente einmalig nachladen.
        const ids = Array.from(
          new Set([...due.faellig, ...due.anstehend].map((r) => r.document)),
        );
        const entries = await Promise.all(
          ids.map(async (id) => {
            try {
              const doc = await getDocument(id);
              return [id, doc.title] as const;
            } catch {
              return [id, `Dokument #${id}`] as const;
            }
          }),
        );
        if (active) setTitles(Object.fromEntries(entries));
      })
      .catch((e) => active && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [days]);

  function renderGroup(title: string, rows: Reminder[], overdue: boolean) {
    return (
      <section className="due-group">
        <h3 className="due-group__title">
          {title} <span className="muted">({rows.length})</span>
        </h3>
        {rows.length === 0 ? (
          <p className="muted">Keine Einträge.</p>
        ) : (
          <ul className="due-list">
            {rows.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  className="due-item"
                  onClick={() => onOpenDocument(r.document)}
                >
                  <span
                    className={`reminder-badge reminder-badge--${overdue ? "overdue" : "open"}`}
                  >
                    {formatDateOnly(r.remind_on)}
                  </span>
                  <span className="due-item__title">
                    {titles[r.document] ?? `Dokument #${r.document}`}
                  </span>
                  {r.note && <span className="due-item__note muted">{r.note}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  }

  return (
    <div className="due-view">
      <div className="due-view__head">
        <p className="muted" style={{ marginTop: 0 }}>
          Offene Wiedervorlagen zu deinen Dokumenten. „Fällig" umfasst heutige
          und überfällige Erinnerungen, „Anstehend" die nächsten Tage.
        </p>
        <label className="filter">
          <span>Vorschau</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {HORIZON_CHOICES.map((c) => (
              <option key={c} value={c}>
                {c} Tage
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading && <p className="muted">Lade …</p>}
      {error && <p className="status status--error">{error}</p>}

      {!loading && !error && (
        <>
          {renderGroup("Fällig", faellig, true)}
          {renderGroup("Anstehend", anstehend, false)}
        </>
      )}
    </div>
  );
}
