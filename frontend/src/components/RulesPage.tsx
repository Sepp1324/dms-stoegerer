import { useEffect, useState } from "react";
import {
  createRule,
  deleteRule,
  getFolders,
  getRules,
  simulateExistingRule,
  simulateRuleDraft,
  type ClassificationRule,
  type ClassificationRulePayload,
  type ClassificationRuleSimulation,
  type FolderRef,
} from "../api";

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function riskLabel(risk: ClassificationRuleSimulation["risk"]) {
  if (risk === "high") return "hoch";
  if (risk === "medium") return "mittel";
  return "niedrig";
}

function SimulationPanel({
  result,
  title,
}: {
  result: ClassificationRuleSimulation | null;
  title: string;
}) {
  if (!result) return null;
  return (
    <section className={`rule-sim rule-sim--${result.risk}`}>
      <div className="rule-sim__head">
        <div>
          <p className="eyebrow">Regel-Simulator</p>
          <h3>{title}</h3>
        </div>
        <strong>{result.impact_score}% Impact Score</strong>
      </div>
      <div className="rule-sim__metrics">
        <span>
          <strong>{result.matched}</strong>
          Treffer
        </span>
        <span>
          <strong>{result.would_update}</strong>
          würde ändern
        </span>
        <span>
          <strong>{result.conflicts}</strong>
          Konflikte
        </span>
        <span>
          <strong>{riskLabel(result.risk)}</strong>
          Risiko
        </span>
      </div>
      {result.warnings.length > 0 && (
        <div className="rule-sim__warnings">
          {result.warnings.map((warning) => (
            <span key={warning}>{warning}</span>
          ))}
        </div>
      )}
      {result.matches.length > 0 ? (
        <div className="rule-sim__matches">
          {result.matches.slice(0, 8).map((match) => (
            <article key={match.id} className="rule-sim-match">
              <div>
                <strong>{match.title}</strong>
                <small>
                  {match.asn_label ?? `#${match.id}`}
                  {match.correspondent_name ? ` · ${match.correspondent_name}` : ""}
                  {match.document_type_name ? ` · ${match.document_type_name}` : ""}
                </small>
              </div>
              <div className="rule-sim-match__changes">
                {match.would_change.map((change, index) => (
                  <span key={`change-${match.id}-${change.field}-${index}`}>
                    {change.field}: {change.to ?? change.add?.join(", ")}
                  </span>
                ))}
                {match.conflicts.map((change, index) => (
                  <span
                    className="rule-sim-match__conflict"
                    key={`conflict-${match.id}-${change.field}-${index}`}
                  >
                    {change.field}: {change.current} → {change.to}
                  </span>
                ))}
                {match.would_change.length === 0 && match.conflicts.length === 0 && (
                  <span>bereits passend</span>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <p className="muted">Keine Treffer im sichtbaren Dokumentbestand.</p>
      )}
    </section>
  );
}

export default function RulesPage({ canEdit }: { canEdit: boolean }) {
  const [rules, setRules] = useState<ClassificationRule[]>([]);
  const [folders, setFolders] = useState<FolderRef[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Formular
  const [name, setName] = useState("");
  const [priority, setPriority] = useState(100);
  const [contains, setContains] = useState("");
  const [regex, setRegex] = useState("");
  const [thenType, setThenType] = useState("");
  const [thenCorr, setThenCorr] = useState("");
  const [thenPath, setThenPath] = useState("");
  const [thenFolder, setThenFolder] = useState("");
  const [thenTags, setThenTags] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [simulation, setSimulation] =
    useState<ClassificationRuleSimulation | null>(null);
  const [simulationTitle, setSimulationTitle] = useState("");
  const [simulating, setSimulating] = useState(false);

  function load() {
    setLoading(true);
    Promise.all([getRules(), getFolders()])
      .then(([ruleItems, folderItems]) => {
        setRules(ruleItems);
        setFolders(folderItems);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  const canSave =
    name.trim() &&
    (contains.trim() || regex.trim()) &&
    (thenType.trim() ||
      thenCorr.trim() ||
      thenPath.trim() ||
      thenFolder.trim() ||
      thenTags.trim());

  function draftPayload(): ClassificationRulePayload {
    const match: ClassificationRule["match"] = {};
    if (contains.trim()) match.text_contains = splitList(contains);
    if (regex.trim()) match.text_regex = regex.trim();
    const then: ClassificationRule["then"] = {};
    if (thenType.trim()) then.document_type = thenType.trim();
    if (thenCorr.trim()) then.correspondent = thenCorr.trim();
    if (thenPath.trim()) then.storage_path = thenPath.trim();
    if (thenFolder.trim()) then.folder = thenFolder.trim();
    if (thenTags.trim()) then.tags = splitList(thenTags);
    return { name: name.trim() || "Regelentwurf", priority, enabled: true, match, then };
  }

  async function create() {
    setSaving(true);
    setSaveError(null);
    try {
      await createRule(draftPayload());
      setName("");
      setContains("");
      setRegex("");
      setThenType("");
      setThenCorr("");
      setThenPath("");
      setThenFolder("");
      setThenTags("");
      setPriority(100);
      load();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function simulateDraft() {
    setSimulating(true);
    setSaveError(null);
    try {
      const payload = draftPayload();
      const result = await simulateRuleDraft(payload);
      setSimulation(result);
      setSimulationTitle(payload.name);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSimulating(false);
    }
  }

  async function simulateStored(rule: ClassificationRule) {
    setSimulating(true);
    setError(null);
    try {
      const result = await simulateExistingRule(rule.id);
      setSimulation(result);
      setSimulationTitle(rule.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSimulating(false);
    }
  }

  async function remove(id: number) {
    await deleteRule(id);
    load();
  }

  return (
    <div className="rules-view">
      <p className="muted" style={{ marginTop: 0 }}>
        Regeln werden nach dem OCR automatisch angewendet und setzen Metadaten
        direkt (nachvollziehbar). Fehlende Korrespondenten/Typen/Tags werden dabei
        angelegt.
      </p>

      {canEdit && (
        <section className="card rule-form">
          <h3 style={{ marginTop: 0 }}>Neue Regel</h3>
          <div className="rule-grid">
            <label>
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="z. B. Rechnungen" />
            </label>
            <label>
              Priorität
              <input
                type="number"
                value={priority}
                onChange={(e) => setPriority(Number(e.target.value) || 0)}
              />
            </label>
          </div>

          <p className="rule-section">Wenn …</p>
          <div className="rule-grid">
            <label>
              Text enthält (Wörter, Komma-getrennt)
              <input
                value={contains}
                onChange={(e) => setContains(e.target.value)}
                placeholder="Rechnung, Invoice"
              />
            </label>
            <label>
              oder Regex (optional)
              <input value={regex} onChange={(e) => setRegex(e.target.value)} placeholder="SR-\d+" />
            </label>
          </div>

          <p className="rule-section">Dann setze …</p>
          <div className="rule-grid">
            <label>
              Typ
              <input value={thenType} onChange={(e) => setThenType(e.target.value)} placeholder="Rechnung" />
            </label>
            <label>
              Korrespondent
              <input value={thenCorr} onChange={(e) => setThenCorr(e.target.value)} placeholder="Stadtwerke" />
            </label>
            <label>
              Ablagepfad
              <input value={thenPath} onChange={(e) => setThenPath(e.target.value)} placeholder="Rechnungen" />
            </label>
            <label>
              Ordner
              <input
                value={thenFolder}
                onChange={(e) => setThenFolder(e.target.value)}
                placeholder="Versicherungen / Wüstenrot"
                list="rule-folder-options"
              />
              <datalist id="rule-folder-options">
                {folders.map((folder) => (
                  <option key={folder.id} value={folder.full_path} />
                ))}
              </datalist>
            </label>
            <label>
              Tags (Komma-getrennt)
              <input value={thenTags} onChange={(e) => setThenTags(e.target.value)} placeholder="Finanzen" />
            </label>
          </div>

          {saveError && <p className="status status--error">{saveError}</p>}
          <div className="rule-form__actions">
            <button onClick={simulateDraft} disabled={simulating || !canSave}>
              {simulating ? "Simuliere …" : "Regel simulieren"}
            </button>
            <button onClick={create} disabled={saving || !canSave}>
              {saving ? "Anlegen …" : "Regel anlegen"}
            </button>
          </div>
        </section>
      )}

      <SimulationPanel result={simulation} title={simulationTitle} />

      {loading && <p className="muted">Lade …</p>}
      {error && <p className="status status--error">{error}</p>}
      {!loading && !error && rules.length === 0 && (
        <p className="muted">Noch keine Regeln.</p>
      )}

      {rules.map((r) => (
        <div key={r.id} className="card rule-item">
          <div className="rule-item__head">
            <span className="rule-item__name">
              {r.name}
              {!r.enabled && <span className="muted"> (inaktiv)</span>}
            </span>
            <span className="muted">Priorität {r.priority}</span>
            {canEdit && (
              <>
                <button className="link" onClick={() => simulateStored(r)}>
                  Simulieren
                </button>
                <button className="link" onClick={() => remove(r.id)}>
                  Löschen
                </button>
              </>
            )}
          </div>
          <div className="rule-item__body">
            <span className="muted">Wenn:</span>{" "}
            {r.match.text_contains?.length
              ? `Text enthält „${r.match.text_contains.join("“ / „")}“`
              : ""}
            {r.match.text_regex ? ` Regex ${r.match.text_regex}` : ""}
            <br />
            <span className="muted">Dann:</span>{" "}
            {[
              r.then.document_type && `Typ = ${r.then.document_type}`,
              r.then.correspondent && `Korrespondent = ${r.then.correspondent}`,
              r.then.storage_path && `Ablage = ${r.then.storage_path}`,
              r.then.folder && `Ordner = ${r.then.folder}`,
              r.then.tags?.length && `Tags = ${r.then.tags.join(", ")}`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </div>
        </div>
      ))}
    </div>
  );
}
