import { useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";

// Tab-Definition der rechten Info-/Aktionsspalte (STOAA-430). Reihenfolge ist
// verbindlich; ``ai`` erscheint nur bei Schreibrecht (KI-Panel ist canEdit-only).
export type TabId =
  | "overview"
  | "briefing"
  | "entities"
  | "similar"
  | "versions"
  | "workbench"
  | "ai"
  | "reminder"
  | "freigabe"
  | "fields"
  | "audit";

export const DETAIL_TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "Übersicht" },
  { id: "briefing", label: "Briefing" },
  { id: "entities", label: "Entitäten" },
  { id: "similar", label: "Ähnlich" },
  { id: "versions", label: "Versionen & Verlauf" },
  { id: "workbench", label: "Werkbank" },
  { id: "ai", label: "KI-Vorschläge" },
  { id: "reminder", label: "Wiedervorlage" },
  { id: "freigabe", label: "Freigabe" },
  { id: "fields", label: "Zusatzfelder" },
  { id: "audit", label: "Audit" },
];

// Vollwertiges ARIA-Tab-Widget (STOAA-430): ``role=tablist`` mit ``role=tab``-
// Buttons (aria-selected/aria-controls/id) und Roving-Tabindex. Pfeiltasten
// (Left/Right) + Home/End bewegen Fokus und aktivieren den Tab (Activation
// follows focus); Enter/Space aktiviert nativ über den Button-Klick.
export function DetailTabs({
  tabs,
  active,
  onSelect,
}: {
  tabs: { id: TabId; label: string }[];
  active: TabId;
  onSelect: (t: TabId) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(e: KeyboardEvent, idx: number) {
    let next: number | null = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (idx + 1) % tabs.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp")
      next = (idx - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    if (next === null) return;
    e.preventDefault();
    onSelect(tabs[next].id);
    refs.current[next]?.focus();
  }

  return (
    <div className="detail-tabs" role="tablist" aria-label="Dokumentbereiche">
      {tabs.map((t, i) => {
        const selected = t.id === active;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            id={`dd-tab-${t.id}`}
            aria-selected={selected}
            aria-controls={`dd-panel-${t.id}`}
            tabIndex={selected ? 0 : -1}
            className={`detail-tab ${selected ? "detail-tab--active" : ""}`}
            onClick={() => onSelect(t.id)}
            onKeyDown={(e) => onKeyDown(e, i)}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

// Ein Tab-Panel: **lazy + keep-alive** (Perf). Ein Panel wird erst gemountet,
// wenn es zum ersten Mal aktiv wird – so feuern versteckte/unbesuchte Tabs beim
// Öffnen eines Dokuments KEINE Requests (früher mounteten alle Panels sofort und
// lösten Briefing-/Entitäten-/Dubletten-/Similar-/… -Requests aus). Nach dem
// ersten Besuch bleibt das Panel im DOM (per ``hidden`` ausgeblendet), damit sein
// Zustand/seine Daten erhalten bleiben und ein erneuter Besuch nicht neu lädt.
export function TabPanel({
  id,
  active,
  children,
}: {
  id: TabId;
  active: TabId;
  children: ReactNode;
}) {
  // Latch: einmal aktiv gewesen -> ab dann gemountet. Das Setzen im Render ist
  // idempotent (nur false->true) und daher unbedenklich.
  const activatedRef = useRef(false);
  if (active === id) activatedRef.current = true;

  return (
    <div
      role="tabpanel"
      id={`dd-panel-${id}`}
      aria-labelledby={`dd-tab-${id}`}
      tabIndex={0}
      hidden={active !== id}
    >
      {activatedRef.current ? children : null}
    </div>
  );
}
