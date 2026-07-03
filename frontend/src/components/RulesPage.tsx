import { useEffect, useState } from "react";
import {
  createRule,
  deleteRule,
  getRules,
  type ClassificationRule,
} from "../api";

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function RulesPage({
  onBack,
  canEdit,
}: {
  onBack: () => void;
  canEdit: boolean;
}) {
  const [rules, setRules] = useState<ClassificationRule[]>([]);
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
  const [thenTags, setThenTags] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    getRules()
      .then(setRules)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  const canSave =
    name.trim() &&
    (contains.trim() || regex.trim()) &&
    (thenType.trim() || thenCorr.trim() || thenPath.trim() || thenTags.trim());

  async function create() {
    setSaving(true);
    setSaveError(null);
    try {
      const match: ClassificationRule["match"] = {};
      if (contains.trim()) match.text_contains = splitList(contains);
      if (regex.trim()) match.text_regex = regex.trim();
      const then: ClassificationRule["then"] = {};
      if (thenType.trim()) then.document_type = thenType.trim();
      if (thenCorr.trim()) then.correspondent = thenCorr.trim();
      if (thenPath.trim()) then.storage_path = thenPath.trim();
      if (thenTags.trim()) then.tags = splitList(thenTags);

      await createRule({ name: name.trim(), priority, enabled: true, match, then });
      setName("");
      setContains("");
      setRegex("");
      setThenType("");
      setThenCorr("");
      setThenPath("");
      setThenTags("");
      setPriority(100);
      load();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: number) {
    await deleteRule(id);
    load();
  }

  return (
    <div className="shell">
      <header className="topbar">
        <button className="link" onClick={onBack}>
          ← Zurück zu den Dokumenten
        </button>
        <h1 style={{ fontSize: "1.2rem" }}>Klassifizierungsregeln</h1>
      </header>

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
              Tags (Komma-getrennt)
              <input value={thenTags} onChange={(e) => setThenTags(e.target.value)} placeholder="Finanzen" />
            </label>
          </div>

          {saveError && <p className="status status--error">{saveError}</p>}
          <button onClick={create} disabled={saving || !canSave}>
            {saving ? "Anlegen …" : "Regel anlegen"}
          </button>
        </section>
      )}

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
              <button className="link" onClick={() => remove(r.id)}>
                Löschen
              </button>
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
