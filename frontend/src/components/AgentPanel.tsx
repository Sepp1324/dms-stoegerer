import { useState } from "react";

import {
  agentExecute,
  agentPlan,
  type AgentAction,
  type AgentExecuteResult,
} from "../api";

/**
 * Copilot-Agent: Der Nutzer gibt eine Anweisung ein, die KI schlägt einen Plan
 * aus sicheren Aktionen vor (Tag/Notiz/Wiedervorlage), der Nutzer wählt aus und
 * bestätigt – erst dann führt das Backend deterministisch und owner-gescoped aus.
 */
export default function AgentPanel() {
  const [instruction, setInstruction] = useState("");
  const [planning, setPlanning] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);
  const [actions, setActions] = useState<AgentAction[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [executing, setExecuting] = useState(false);
  const [result, setResult] = useState<AgentExecuteResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function plan() {
    if (instruction.trim().length < 3) return;
    setPlanning(true);
    setError(null);
    setResult(null);
    setActions([]);
    try {
      const res = await agentPlan(instruction.trim());
      setAnswer(res.answer);
      setActions(res.actions);
      setSelected(new Set(res.actions.map((_, i) => i)));
    } catch {
      setError("Planung fehlgeschlagen.");
    } finally {
      setPlanning(false);
    }
  }

  async function execute() {
    const chosen = actions.filter((_, i) => selected.has(i));
    if (chosen.length === 0) return;
    setExecuting(true);
    setError(null);
    try {
      const res = await agentExecute(
        chosen.map((a) => ({ action: a.action, document: a.document, params: a.params })),
      );
      setResult(res);
      setActions([]); // Plan verbraucht – neue Anweisung für weitere Aktionen
    } catch {
      setError("Ausführen fehlgeschlagen.");
    } finally {
      setExecuting(false);
    }
  }

  function toggle(i: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }

  return (
    <section className="card agent-panel">
      <h3>Agent – Aktionen ausführen</h3>
      <p className="muted agent-panel__hint">
        Sag, was zu tun ist – z. B. „Markiere den Stromvertrag zur Kündigung bis
        30.09." Der Agent schlägt Aktionen vor; ausgeführt wird erst nach deiner
        Bestätigung (nur eigene Dokumente).
      </p>

      <form
        className="agent-panel__form"
        onSubmit={(e) => {
          e.preventDefault();
          void plan();
        }}
      >
        <input
          className="search"
          placeholder="Anweisung …"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
        />
        <button type="submit" disabled={planning}>
          {planning ? "Plane …" : "Planen"}
        </button>
      </form>

      {error && <p className="form-error">{error}</p>}
      {answer && actions.length === 0 && !result && (
        <p className="muted">{answer}</p>
      )}

      {actions.length > 0 && (
        <>
          <ul className="agent-panel__actions">
            {actions.map((a, i) => (
              <li key={i} className="agent-panel__action">
                <label>
                  <input
                    type="checkbox"
                    checked={selected.has(i)}
                    onChange={() => toggle(i)}
                  />
                  <span>{a.summary}</span>
                </label>
              </li>
            ))}
          </ul>
          <button onClick={execute} disabled={executing || selected.size === 0}>
            {executing ? "Führe aus …" : `${selected.size} Aktion(en) ausführen`}
          </button>
        </>
      )}

      {result && (
        <div className="agent-panel__result">
          {result.applied.length > 0 && (
            <ul className="agent-panel__applied">
              {result.applied.map((a, i) => (
                <li key={i}>✓ {a.summary}</li>
              ))}
            </ul>
          )}
          {result.errors.length > 0 && (
            <ul className="agent-panel__errors">
              {result.errors.map((e, i) => (
                <li key={i} className="form-error">
                  ✕ {e.error}
                </li>
              ))}
            </ul>
          )}
          {result.applied.length === 0 && result.errors.length === 0 && (
            <p className="muted">Nichts ausgeführt.</p>
          )}
        </div>
      )}
    </section>
  );
}
