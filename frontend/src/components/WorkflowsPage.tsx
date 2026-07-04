import { useEffect, useState } from "react";
import {
  createWorkflow,
  deleteWorkflow,
  getCorrespondents,
  getDocumentTypes,
  getStoragePaths,
  getTags,
  getWorkflows,
  type NamedRef,
  type TagRef,
  type Workflow,
  type WorkflowAction,
  type WorkflowActionType,
  type WorkflowTriggerType,
} from "../api";

const SOURCES = ["upload", "consume", "mail", "api"] as const;

const emptyAction = (order: number): WorkflowAction => ({
  order,
  action_type: "assign",
  assign_title: "",
  assign_correspondent: null,
  assign_document_type: null,
  assign_storage_path: null,
  assign_tags: [],
  assign_owner: null,
  assign_custom_fields: {},
  remove_tags: [],
});

function nameOf(list: NamedRef[], id: number | null): string {
  if (id == null) return "";
  return list.find((x) => x.id === id)?.name ?? `#${id}`;
}

export default function WorkflowsPage({ canEdit }: { canEdit: boolean }) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Stammdaten für Dropdowns
  const [correspondents, setCorrespondents] = useState<NamedRef[]>([]);
  const [docTypes, setDocTypes] = useState<NamedRef[]>([]);
  const [storagePaths, setStoragePaths] = useState<NamedRef[]>([]);
  const [tags, setTags] = useState<TagRef[]>([]);

  // Formular
  const [name, setName] = useState("");
  const [order, setOrder] = useState(100);
  const [triggerType, setTriggerType] = useState<WorkflowTriggerType>("document_added");
  const [sources, setSources] = useState<string[]>([]);
  const [filterCorr, setFilterCorr] = useState<number | null>(null);
  const [filterDocType, setFilterDocType] = useState<number | null>(null);
  const [filterHasTags, setFilterHasTags] = useState<number[]>([]);
  const [filterHasNotTags, setFilterHasNotTags] = useState<number[]>([]);
  const [filterPath, setFilterPath] = useState("");
  const [filterTextContains, setFilterTextContains] = useState("");
  const [filterTextRegex, setFilterTextRegex] = useState("");
  const [actions, setActions] = useState<WorkflowAction[]>([emptyAction(10)]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    getWorkflows()
      .then(setWorkflows)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);
  useEffect(() => {
    getCorrespondents().then(setCorrespondents).catch(() => {});
    getDocumentTypes().then(setDocTypes).catch(() => {});
    getStoragePaths().then(setStoragePaths).catch(() => {});
    getTags().then(setTags).catch(() => {});
  }, []);

  function toggleSource(src: string) {
    setSources((cur) =>
      cur.includes(src) ? cur.filter((s) => s !== src) : [...cur, src],
    );
  }
  function toggleTag(list: number[], setList: (v: number[]) => void, id: number) {
    setList(list.includes(id) ? list.filter((t) => t !== id) : [...list, id]);
  }
  function updateAction(idx: number, patch: Partial<WorkflowAction>) {
    setActions((cur) => cur.map((a, i) => (i === idx ? { ...a, ...patch } : a)));
  }
  function addAction() {
    setActions((cur) => [...cur, emptyAction((cur.length + 1) * 10)]);
  }
  function removeAction(idx: number) {
    setActions((cur) => cur.filter((_, i) => i !== idx));
  }

  function resetForm() {
    setName("");
    setOrder(100);
    setTriggerType("document_added");
    setSources([]);
    setFilterCorr(null);
    setFilterDocType(null);
    setFilterHasTags([]);
    setFilterHasNotTags([]);
    setFilterPath("");
    setFilterTextContains("");
    setFilterTextRegex("");
    setActions([emptyAction(10)]);
  }

  const canSave = name.trim() && actions.length > 0;

  async function save() {
    setSaving(true);
    setSaveError(null);
    try {
      await createWorkflow({
        name: name.trim(),
        order,
        enabled: true,
        trigger: {
          trigger_type: triggerType,
          sources: sources.join(","),
          filter_path: filterPath.trim(),
          filter_correspondent: filterCorr,
          filter_document_type: filterDocType,
          filter_has_tags: filterHasTags,
          filter_has_not_tags: filterHasNotTags,
          filter_text_contains: filterTextContains.trim(),
          filter_text_regex: filterTextRegex.trim(),
        },
        actions: actions.map((a, i) => ({ ...a, order: (i + 1) * 10 })),
      });
      resetForm();
      load();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: number) {
    await deleteWorkflow(id);
    load();
  }

  return (
    <div className="rules-view">
      <p className="muted" style={{ marginTop: 0 }}>
        Workflows sind das mächtigere Konstrukt neben den Regeln: Ein Trigger
        (Dokument hinzugefügt/aktualisiert) mit Bedingungen führt eine geordnete
        Liste von Aktionen aus. Sie laufen nach dem OCR bzw. beim Metadaten-Update.
      </p>

      {canEdit && (
        <section className="card rule-form">
          <h3 style={{ marginTop: 0 }}>Neuer Workflow</h3>
          <div className="rule-grid">
            <label>
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="z. B. Rechnungen ablegen" />
            </label>
            <label>
              Reihenfolge (kleiner = zuerst)
              <input type="number" value={order} onChange={(e) => setOrder(Number(e.target.value) || 0)} />
            </label>
          </div>

          <p className="rule-section">Trigger</p>
          <div className="rule-grid">
            <label>
              Auslöser
              <select value={triggerType} onChange={(e) => setTriggerType(e.target.value as WorkflowTriggerType)}>
                <option value="document_added">Dokument hinzugefügt</option>
                <option value="document_updated">Dokument aktualisiert</option>
              </select>
            </label>
            <label>
              Quellen (leer = alle)
              <span className="wf-checkboxes">
                {SOURCES.map((src) => (
                  <label key={src} className="wf-checkbox">
                    <input
                      type="checkbox"
                      checked={sources.includes(src)}
                      onChange={() => toggleSource(src)}
                    />
                    {src}
                  </label>
                ))}
              </span>
            </label>
          </div>

          <p className="rule-section">Bedingungen (optional, UND-verknüpft)</p>
          <div className="rule-grid">
            <label>
              Korrespondent
              <select value={filterCorr ?? ""} onChange={(e) => setFilterCorr(e.target.value ? Number(e.target.value) : null)}>
                <option value="">– beliebig –</option>
                {correspondents.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </label>
            <label>
              Dokumenttyp
              <select value={filterDocType ?? ""} onChange={(e) => setFilterDocType(e.target.value ? Number(e.target.value) : null)}>
                <option value="">– beliebig –</option>
                {docTypes.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            </label>
            <label>
              Pfad-Glob
              <input value={filterPath} onChange={(e) => setFilterPath(e.target.value)} placeholder="*/scans/*.pdf" />
            </label>
            <label>
              Text enthält
              <input value={filterTextContains} onChange={(e) => setFilterTextContains(e.target.value)} placeholder="Rechnung" />
            </label>
            <label>
              Text-Regex
              <input value={filterTextRegex} onChange={(e) => setFilterTextRegex(e.target.value)} placeholder="SR-\d+" />
            </label>
          </div>
          <div className="rule-grid">
            <label>
              Muss Tags haben
              <span className="wf-checkboxes">
                {tags.map((t) => (
                  <label key={t.id} className="wf-checkbox">
                    <input
                      type="checkbox"
                      checked={filterHasTags.includes(t.id)}
                      onChange={() => toggleTag(filterHasTags, setFilterHasTags, t.id)}
                    />
                    {t.name}
                  </label>
                ))}
              </span>
            </label>
            <label>
              Darf Tags nicht haben
              <span className="wf-checkboxes">
                {tags.map((t) => (
                  <label key={t.id} className="wf-checkbox">
                    <input
                      type="checkbox"
                      checked={filterHasNotTags.includes(t.id)}
                      onChange={() => toggleTag(filterHasNotTags, setFilterHasNotTags, t.id)}
                    />
                    {t.name}
                  </label>
                ))}
              </span>
            </label>
          </div>

          <p className="rule-section">Aktionen (in Reihenfolge)</p>
          {actions.map((action, idx) => (
            <div key={idx} className="card wf-action">
              <div className="rule-grid">
                <label>
                  Typ
                  <select
                    value={action.action_type}
                    onChange={(e) => updateAction(idx, { action_type: e.target.value as WorkflowActionType })}
                  >
                    <option value="assign">Zuweisen</option>
                    <option value="remove">Entfernen</option>
                  </select>
                </label>
                {actions.length > 1 && (
                  <button className="link" type="button" onClick={() => removeAction(idx)}>
                    Aktion entfernen
                  </button>
                )}
              </div>

              {action.action_type === "assign" ? (
                <>
                  <div className="rule-grid">
                    <label>
                      Titel-Template
                      <input
                        value={action.assign_title}
                        onChange={(e) => updateAction(idx, { assign_title: e.target.value })}
                        placeholder="{correspondent} – {doc_type} {created}"
                      />
                    </label>
                    <label>
                      Korrespondent
                      <select
                        value={action.assign_correspondent ?? ""}
                        onChange={(e) => updateAction(idx, { assign_correspondent: e.target.value ? Number(e.target.value) : null })}
                      >
                        <option value="">– keiner –</option>
                        {correspondents.map((c) => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Dokumenttyp
                      <select
                        value={action.assign_document_type ?? ""}
                        onChange={(e) => updateAction(idx, { assign_document_type: e.target.value ? Number(e.target.value) : null })}
                      >
                        <option value="">– keiner –</option>
                        {docTypes.map((d) => (
                          <option key={d.id} value={d.id}>{d.name}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Ablagepfad
                      <select
                        value={action.assign_storage_path ?? ""}
                        onChange={(e) => updateAction(idx, { assign_storage_path: e.target.value ? Number(e.target.value) : null })}
                      >
                        <option value="">– keiner –</option>
                        {storagePaths.map((s) => (
                          <option key={s.id} value={s.id}>{s.name}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <label>
                    Tags hinzufügen
                    <span className="wf-checkboxes">
                      {tags.map((t) => (
                        <label key={t.id} className="wf-checkbox">
                          <input
                            type="checkbox"
                            checked={action.assign_tags.includes(t.id)}
                            onChange={() => updateAction(idx, {
                              assign_tags: action.assign_tags.includes(t.id)
                                ? action.assign_tags.filter((x) => x !== t.id)
                                : [...action.assign_tags, t.id],
                            })}
                          />
                          {t.name}
                        </label>
                      ))}
                    </span>
                  </label>
                </>
              ) : (
                <label>
                  Tags entfernen
                  <span className="wf-checkboxes">
                    {tags.map((t) => (
                      <label key={t.id} className="wf-checkbox">
                        <input
                          type="checkbox"
                          checked={action.remove_tags.includes(t.id)}
                          onChange={() => updateAction(idx, {
                            remove_tags: action.remove_tags.includes(t.id)
                              ? action.remove_tags.filter((x) => x !== t.id)
                              : [...action.remove_tags, t.id],
                          })}
                        />
                        {t.name}
                      </label>
                    ))}
                  </span>
                </label>
              )}
            </div>
          ))}
          <button type="button" className="link" onClick={addAction}>
            + Aktion hinzufügen
          </button>

          {saveError && <p className="status status--error">{saveError}</p>}
          <div style={{ marginTop: 12 }}>
            <button onClick={save} disabled={saving || !canSave}>
              {saving ? "Anlegen …" : "Workflow anlegen"}
            </button>
          </div>
        </section>
      )}

      {loading && <p className="muted">Lade …</p>}
      {error && <p className="status status--error">{error}</p>}
      {!loading && !error && workflows.length === 0 && (
        <p className="muted">Noch keine Workflows.</p>
      )}

      {workflows.map((wf) => (
        <div key={wf.id} className="card rule-item">
          <div className="rule-item__head">
            <span className="rule-item__name">
              {wf.name}
              {!wf.enabled && <span className="muted"> (inaktiv)</span>}
            </span>
            <span className="muted">Reihenfolge {wf.order}</span>
            {canEdit && (
              <button className="link" onClick={() => remove(wf.id)}>
                Löschen
              </button>
            )}
          </div>
          <div className="rule-item__body">
            <span className="muted">Trigger:</span>{" "}
            {wf.trigger
              ? `${wf.trigger.trigger_type === "document_added" ? "Hinzugefügt" : "Aktualisiert"}${
                  wf.trigger.sources ? ` · Quellen: ${wf.trigger.sources}` : ""
                }${wf.trigger.filter_correspondent ? ` · Korr.: ${nameOf(correspondents, wf.trigger.filter_correspondent)}` : ""}${
                  wf.trigger.filter_text_contains ? ` · Text: „${wf.trigger.filter_text_contains}“` : ""
                }`
              : "—"}
            <br />
            <span className="muted">Aktionen:</span>{" "}
            {wf.actions.length === 0
              ? "—"
              : wf.actions
                  .map((a) =>
                    a.action_type === "assign"
                      ? [
                          a.assign_title && `Titel „${a.assign_title}“`,
                          a.assign_document_type && `Typ = ${nameOf(docTypes, a.assign_document_type)}`,
                          a.assign_correspondent && `Korr. = ${nameOf(correspondents, a.assign_correspondent)}`,
                          a.assign_storage_path && `Ablage = ${nameOf(storagePaths, a.assign_storage_path)}`,
                          a.assign_tags.length && `+Tags(${a.assign_tags.length})`,
                        ]
                          .filter(Boolean)
                          .join(", ") || "Zuweisen"
                      : `Entferne Tags(${a.remove_tags.length})`,
                  )
                  .join(" → ")}
          </div>
        </div>
      ))}
    </div>
  );
}
