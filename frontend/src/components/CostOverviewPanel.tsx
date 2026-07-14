import { useEffect, useState } from "react";

import { getCostOverview, type CostBreakdownEntry, type CostOverview } from "../api";

function money(value: number, currency: string): string {
  return new Intl.NumberFormat("de-AT", {
    style: "currency",
    currency: currency || "EUR",
    maximumFractionDigits: 2,
  }).format(value);
}

/**
 * Fixkosten-/Ausgabenüberblick: normalisiert die aktiven Vertragsbeträge auf
 * Monat/Jahr, zeigt Summen je Währung, Aufschlüsselung nach Kategorie/Anbieter
 * und die nächsten fälligen Zahlungen. Rein aus den strukturierten Vertragsdaten.
 */
export default function CostOverviewPanel() {
  const [data, setData] = useState<CostOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    let active = true;
    getCostOverview()
      .then((res) => active && setData(res))
      .catch(() => active && setError("Ausgabenüberblick konnte nicht geladen werden."))
      .finally(() => active && setBusy(false));
    return () => {
      active = false;
    };
  }, []);

  if (busy) {
    return (
      <section className="card cost-overview">
        <p className="muted">Berechne Fixkosten …</p>
      </section>
    );
  }
  if (error) {
    return (
      <section className="card cost-overview">
        <p className="form-error">{error}</p>
      </section>
    );
  }
  if (!data) return null;

  const { currency_totals, by_type, by_provider, upcoming, coverage } = data;
  const hasData = currency_totals.length > 0;
  const maxTypeMonthly = Math.max(1, ...by_type.map((t) => t.monthly));

  return (
    <section className="card cost-overview">
      <div className="cost-overview__head">
        <h2>Fixkosten-Überblick</h2>
        <span className="muted cost-overview__coverage">
          {coverage.recurring} von {coverage.active} aktiven Verträgen eingerechnet
          {coverage.unknown > 0 ? ` · ${coverage.unknown} ohne Betrag/Turnus` : ""}
          {coverage.one_time > 0 ? ` · ${coverage.one_time} einmalig` : ""}
        </span>
      </div>

      {!hasData && (
        <p className="muted">
          Noch keine laufenden Beträge erkannt. Sobald Verträge Betrag + Turnus
          tragen, erscheint hier die monatliche/jährliche Summe.
        </p>
      )}

      {hasData && (
        <>
          <div className="cost-overview__totals">
            {currency_totals.map((total) => (
              <div key={total.currency} className="cost-total">
                <div className="cost-total__monthly">{money(total.monthly, total.currency)}</div>
                <div className="muted cost-total__label">pro Monat</div>
                <div className="cost-total__yearly">
                  {money(total.yearly, total.currency)} / Jahr · {total.contracts} Verträge
                </div>
              </div>
            ))}
          </div>

          {by_type.length > 0 && (
            <div className="cost-breakdown">
              <h3>Nach Kategorie</h3>
              {by_type.map((entry) => (
                <BreakdownBar
                  key={`${entry.type}-${entry.currency}`}
                  label={entry.label ?? entry.type ?? "—"}
                  entry={entry}
                  max={maxTypeMonthly}
                />
              ))}
            </div>
          )}

          {by_provider.length > 0 && (
            <div className="cost-breakdown">
              <h3>Top-Anbieter</h3>
              {by_provider.map((entry) => (
                <BreakdownBar
                  key={`${entry.provider}-${entry.currency}`}
                  label={entry.provider ?? "—"}
                  entry={entry}
                  max={maxTypeMonthly}
                />
              ))}
            </div>
          )}
        </>
      )}

      {upcoming.length > 0 && (
        <div className="cost-upcoming">
          <h3>Nächste Zahlungen ({data.upcoming_days} Tage)</h3>
          <ul>
            {upcoming.map((item) => (
              <li key={`${item.document}-${item.due_on}`} className="cost-upcoming__row">
                <span className="cost-upcoming__date">
                  {new Date(item.due_on).toLocaleDateString("de-AT")}
                </span>
                <span className="cost-upcoming__name">
                  {item.provider || item.document_title}
                  <span className="muted"> · {item.type_label}</span>
                </span>
                <span className="cost-upcoming__amount">
                  {item.amount != null ? money(item.amount, item.currency) : "—"}
                  <span className="muted"> / {item.cycle_label}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function BreakdownBar({
  label,
  entry,
  max,
}: {
  label: string;
  entry: CostBreakdownEntry;
  max: number;
}) {
  const pct = Math.max(4, Math.round((entry.monthly / max) * 100));
  return (
    <div className="cost-bar">
      <div className="cost-bar__label">
        {label}
        <span className="muted"> · {entry.count}</span>
      </div>
      <div className="cost-bar__track">
        <div className="cost-bar__fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="cost-bar__value">{money(entry.monthly, entry.currency)}/Mo</div>
    </div>
  );
}
