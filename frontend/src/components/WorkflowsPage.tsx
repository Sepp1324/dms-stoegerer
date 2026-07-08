import { useEffect, useMemo, useState } from "react";
import {
  createWorkflow,
  deleteWorkflow,
  getCorrespondents,
  getDocumentTypes,
  getStoragePaths,
  getTags,
  getWorkflows,
  updateWorkflow,
  type NamedRef,
  type TagRef,
  type Workflow,
  type WorkflowAction,
  type WorkflowActionType,
  type WorkflowPayload,
  type WorkflowTrigger,
  type WorkflowTriggerType,
} from "../api";

const SOURCES = ["upload", "consume", "mail", "api"] as const;

type FormState = {
  id: number | null;
  name: string;
  order: number;
  enabled: boolean;
  trigger: WorkflowTrigger;
  actions: WorkflowAction[];
};

type RefData = {
  correspondents: NamedRef[];
  docTypes: NamedRef[];
  storagePaths: NamedRef[];
  tags: TagRef[];
};

const emptyTrigger = (): WorkflowTrigger => ({
  trigger_type: "document_added",
  sources: "",
  filter_path: "",
  filter_correspondent: null,
  filter_document_type: null,
  filter_has_tags: [],
  filter_has_not_tags: [],
  filter_text_contains: "",
  filter_text_regex: "",
});

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

const emptyForm = (): FormState => ({
  id: null,
  name: "",
  order: 100,
  enabled: true,
  trigger: emptyTrigger(),
  actions: [emptyAction(10)],
});

function nameOf(list: NamedRef[], id: number | null): string {
  if (id == null) return "";
  return list.find((x) => x.id === id)?.name ?? `#${id}`;
}

function tagName(tags: TagRef[], id: number): string {
  return tags.find((tag) => tag.id === id)?.name ?? `#${id}`;
}

function sourcesFromString(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function workflowToForm(workflow: Workflow): FormState {
  return {
    id: workflow.id,
    name: workflow.name,
    order: workflow.order,
    enabled: workflow.enabled,
    trigger: workflow.trigger ?? emptyTrigger(),
    actions:
      workflow.actions.length > 0
        ? workflow.actions.map((action, idx) => ({ ...action, order: (idx + 1) * 10 }))
        : [emptyAction(10)],
  };
}

function formToPayload(form: FormState): WorkflowPayload {
  return {
    name: form.name.trim(),
    order: form.order,
    enabled: form.enabled,
    trigger: {
      ...form.trigger,
      sources: sourcesFromString(form.trigger.sources).join(","),
      filter_path: form.trigger.filter_path.trim(),
      filter_text_contains: form.trigger.filter_text_contains.trim(),
      filter_text_regex: form.trigger.filter_text_regex.trim(),
    },
    actions: form.actions.map((action, idx) => ({
      ...action,
      order: (idx + 1) * 10,
    })),
  };
}

function triggerSummary(trigger: WorkflowTrigger, refs: RefData): string[] {
  const parts = [
    trigger.trigger_type === "document_added"
      ? "wenn ein Dokument fertig hinzugefügt wurde"
      : "wenn Metadaten eines Dokuments aktualisiert wurden",
  ];
  if (trigger.sources) parts.push(`Quelle: ${trigger.sources}`);
  if (trigger.filter_correspondent) {
    parts.push(`Korrespondent: ${nameOf(refs.correspondents, trigger.filter_correspondent)}`);
  }
  if (trigger.filter_document_type) {
    parts.push(`Typ: ${nameOf(refs.docTypes, trigger.filter_document_type)}`);
  }
  if (trigger.filter_has_tags.length) {
    parts.push(`hat Tags: ${trigger.filter_has_tags.map((id) => tagName(refs.tags, id)).join(", ")}`);
  }
  if (trigger.filter_has_not_tags.length) {
    parts.push(`ohne Tags: ${trigger.filter_has_not_tags.map((id) => tagName(refs.tags, id)).join(", ")}`);
  }
  if (trigger.filter_path) parts.push(`Pfad: ${trigger.filter_path}`);
  if (trigger.filter_text_contains) parts.push(`Text enthält: ${trigger.filter_text_contains}`);
  if (trigger.filter_text_regex) parts.push(`Regex: ${trigger.filter_text_regex}`);
  return parts;
}

function actionSummary(action: WorkflowAction, refs: RefData): string {
  if (action.action_type === "remove") {
    const removed = action.remove_tags.map((id) => tagName(refs.tags, id)).join(", ");
    return removed ? `entferne Tags: ${removed}` : "entferne Metadaten";
  }

  const parts = [
    action.assign_title && `Titel: ${action.assign_title}`,
    action.assign_correspondent &&
      `Korrespondent: ${nameOf(refs.correspondents, action.assign_correspondent)}`,
    action.assign_document_type &&
      `Typ: ${nameOf(refs.docTypes, action.assign_document_type)}`,
    action.assign_storage_path &&
      `Ablage: ${nameOf(refs.storagePaths, action.assign_storage_path)}`,
    action.assign_tags.length &&
      `Tags: ${action.assign_tags.map((id) => tagName(refs.tags, id)).join(", ")}`,
  ].filter(Boolean);
  return parts.join(" · ") || "weise Metadaten zu";
}

function currentSources(trigger: WorkflowTrigger): string[] {
  return sourcesFromString(trigger.sources);
}

function workflowStats(workflows: Workflow[]) {
  return {
    total: workflows.length,
    active: workflows.filter((workflow) => workflow.enabled).length,
    actions: workflows.reduce((sum, workflow) => sum + workflow.actions.length, 0),
  };
}

export default function WorkflowsPage({ canEdit }: { canEdit: boolean }) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [refs, setRefs] = useState<RefData>({
    correspondents: [],
    docTypes: [],
    storagePaths: [],
    tags: [],
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const stats = useMemo(() => workflowStats(workflows), [workflows]);
  const selected = workflows.find((workflow) => workflow.id === selectedId) ?? null;
  const canSave = canEdit && form.name.trim() && form.actions.length > 0;
  const triggerParts = triggerSummary(form.trigger, refs);

  function load() {
    setLoading(true);
    setError(null);
    Promise.all([
      getWorkflows(),
      getCorrespondents(),
      getDocumentTypes(),
      getStoragePaths(),
      getTags(),
    ])
      .then(([workflowItems, correspondents, docTypes, storagePaths, tags]) => {
        setWorkflows(workflowItems);
        setRefs({ correspondents, docTypes, storagePaths, tags });
        if (workflowItems.length > 0 && selectedId === null) {
          setSelectedId(workflowItems[0].id);
          setForm(workflowToForm(workflowItems[0]));
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  function selectWorkflow(workflow: Workflow) {
    setSelectedId(workflow.id);
    setForm(workflowToForm(workflow));
    setSaveError(null);
  }

  function newWorkflow() {
    setSelectedId(null);
    setForm(emptyForm());
    setSaveError(null);
  }

  function patchForm(patch: Partial<FormState>) {
    setForm((current) => ({ ...current, ...patch }));
  }

  function patchTrigger(patch: Partial<WorkflowTrigger>) {
    setForm((current) => ({
      ...current,
      trigger: { ...current.trigger, ...patch },
    }));
  }

  function patchAction(index: number, patch: Partial<WorkflowAction>) {
    setForm((current) => ({
      ...current,
      actions: current.actions.map((action, idx) =>
        idx === index ? { ...action, ...patch } : action,
      ),
    }));
  }

  function toggleSource(source: string) {
    const current = currentSources(form.trigger);
    const next = current.includes(source)
      ? current.filter((item) => item !== source)
      : [...current, source];
    patchTrigger({ sources: next.join(",") });
  }

  function toggleTag(
    values: number[],
    onChange: (next: number[]) => void,
    tagId: number,
  ) {
    onChange(
      values.includes(tagId)
        ? values.filter((id) => id !== tagId)
        : [...values, tagId],
    );
  }

  function addAction() {
    setForm((current) => ({
      ...current,
      actions: [...current.actions, emptyAction((current.actions.length + 1) * 10)],
    }));
  }

  function removeAction(index: number) {
    setForm((current) => ({
      ...current,
      actions: current.actions.filter((_action, idx) => idx !== index),
    }));
  }

  async function save() {
    if (!canSave) return;
    setSaving(true);
    setSaveError(null);
    try {
      const payload = formToPayload(form);
      const saved =
        form.id === null
          ? await createWorkflow(payload)
          : await updateWorkflow(form.id, payload);
      const next = await getWorkflows();
      setWorkflows(next);
      setSelectedId(saved.id);
      setForm(workflowToForm(saved));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function toggleEnabled(workflow: Workflow) {
    const payload: WorkflowPayload = {
      ...workflow,
      enabled: !workflow.enabled,
      trigger: workflow.trigger ?? emptyTrigger(),
      actions: workflow.actions,
    };
    const saved = await updateWorkflow(workflow.id, payload);
    setWorkflows((current) =>
      current.map((item) => (item.id === workflow.id ? saved : item)),
    );
    if (selectedId === workflow.id) setForm(workflowToForm(saved));
  }

  async function cloneWorkflow(workflow: Workflow) {
    const cloned = await createWorkflow({
      name: `${workflow.name} Kopie`,
      order: workflow.order + 1,
      enabled: false,
      trigger: workflow.trigger ?? emptyTrigger(),
      actions: workflow.actions,
    });
    const next = await getWorkflows();
    setWorkflows(next);
    setSelectedId(cloned.id);
    setForm(workflowToForm(cloned));
  }

  async function removeWorkflow(workflow: Workflow) {
    await deleteWorkflow(workflow.id);
    const next = await getWorkflows();
    setWorkflows(next);
    const first = next[0] ?? null;
    setSelectedId(first?.id ?? null);
    setForm(first ? workflowToForm(first) : emptyForm());
  }

  return (
    <section className="workflow-designer">
      <div className="workflow-hero">
        <div>
          <p className="eyebrow">Workflow Designer</p>
          <h2>Automationen für Dokumente</h2>
        </div>
        <div className="workflow-stats" aria-label="Workflow Statistik">
          <span>
            <strong>{stats.total}</strong>
            Workflows
          </span>
          <span>
            <strong>{stats.active}</strong>
            aktiv
          </span>
          <span>
            <strong>{stats.actions}</strong>
            Aktionen
          </span>
        </div>
      </div>

      {error && (
        <div className="state state--error">
          <strong>Workflows konnten nicht geladen werden.</strong>
          <span>{error}</span>
        </div>
      )}

      <div className="workflow-layout">
        <aside className="workflow-sidebar">
          <div className="workflow-sidebar__head">
            <div>
              <strong>Bibliothek</strong>
              <span className="muted">{workflows.length} gespeichert</span>
            </div>
            {canEdit && (
              <button type="button" className="link" onClick={newWorkflow}>
                Neu
              </button>
            )}
          </div>

          {loading ? (
            <div className="workflow-empty">Lade Workflows …</div>
          ) : workflows.length === 0 ? (
            <div className="workflow-empty">Noch keine Workflows.</div>
          ) : (
            <div className="workflow-list">
              {workflows.map((workflow) => (
                <button
                  type="button"
                  key={workflow.id}
                  className={`workflow-list__item${
                    selectedId === workflow.id ? " workflow-list__item--active" : ""
                  }`}
                  onClick={() => selectWorkflow(workflow)}
                >
                  <span className="workflow-list__title">
                    {workflow.name}
                    {!workflow.enabled && <small>inaktiv</small>}
                  </span>
                  <span className="workflow-list__meta">
                    Reihenfolge {workflow.order} · {workflow.actions.length} Aktion
                    {workflow.actions.length === 1 ? "" : "en"}
                  </span>
                </button>
              ))}
            </div>
          )}
        </aside>

        <main className="workflow-canvas">
          <div className="workflow-toolbar">
            <div>
              <strong>{form.id ? "Workflow bearbeiten" : "Neuer Workflow"}</strong>
              {selected && (
                <span className="muted">
                  Zuletzt geladen: {selected.enabled ? "aktiv" : "inaktiv"}
                </span>
              )}
            </div>
            <div className="workflow-toolbar__actions">
              {selected && canEdit && (
                <>
                  <button type="button" className="link" onClick={() => toggleEnabled(selected)}>
                    {selected.enabled ? "Deaktivieren" : "Aktivieren"}
                  </button>
                  <button type="button" className="link" onClick={() => cloneWorkflow(selected)}>
                    Klonen
                  </button>
                  <button type="button" className="link link--danger" onClick={() => removeWorkflow(selected)}>
                    Löschen
                  </button>
                </>
              )}
            </div>
          </div>

          <section className="workflow-panel">
            <div className="workflow-section-head">
              <span className="workflow-step">1</span>
              <div>
                <h3>Grunddaten</h3>
                <p>Name, Reihenfolge und Aktivstatus des Workflows.</p>
              </div>
            </div>
            <div className="workflow-form-grid">
              <label>
                Name
                <input
                  value={form.name}
                  onChange={(event) => patchForm({ name: event.target.value })}
                  placeholder="z. B. Rechnungen automatisch einordnen"
                  disabled={!canEdit}
                />
              </label>
              <label>
                Reihenfolge
                <input
                  type="number"
                  value={form.order}
                  onChange={(event) => patchForm({ order: Number(event.target.value) || 0 })}
                  disabled={!canEdit}
                />
              </label>
              <label className="workflow-switch">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(event) => patchForm({ enabled: event.target.checked })}
                  disabled={!canEdit}
                />
                Aktiv
              </label>
            </div>
          </section>

          <section className="workflow-panel">
            <div className="workflow-section-head">
              <span className="workflow-step">2</span>
              <div>
                <h3>Wenn</h3>
                <p>Trigger und Bedingungen werden UND-verknüpft.</p>
              </div>
            </div>
            <div className="workflow-form-grid">
              <label>
                Auslöser
                <select
                  value={form.trigger.trigger_type}
                  onChange={(event) =>
                    patchTrigger({ trigger_type: event.target.value as WorkflowTriggerType })
                  }
                  disabled={!canEdit}
                >
                  <option value="document_added">Dokument hinzugefügt</option>
                  <option value="document_updated">Dokument aktualisiert</option>
                </select>
              </label>
              <fieldset>
                <legend>Quellen</legend>
                <div className="workflow-chip-row">
                  {SOURCES.map((source) => (
                    <label className="workflow-chip" key={source}>
                      <input
                        type="checkbox"
                        checked={currentSources(form.trigger).includes(source)}
                        onChange={() => toggleSource(source)}
                        disabled={!canEdit}
                      />
                      {source}
                    </label>
                  ))}
                </div>
              </fieldset>
              <label>
                Korrespondent
                <select
                  value={form.trigger.filter_correspondent ?? ""}
                  onChange={(event) =>
                    patchTrigger({
                      filter_correspondent: event.target.value
                        ? Number(event.target.value)
                        : null,
                    })
                  }
                  disabled={!canEdit}
                >
                  <option value="">beliebig</option>
                  {refs.correspondents.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Dokumenttyp
                <select
                  value={form.trigger.filter_document_type ?? ""}
                  onChange={(event) =>
                    patchTrigger({
                      filter_document_type: event.target.value
                        ? Number(event.target.value)
                        : null,
                    })
                  }
                  disabled={!canEdit}
                >
                  <option value="">beliebig</option>
                  {refs.docTypes.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Pfad-Glob
                <input
                  value={form.trigger.filter_path}
                  onChange={(event) => patchTrigger({ filter_path: event.target.value })}
                  placeholder="*/scans/*.pdf"
                  disabled={!canEdit}
                />
              </label>
              <label>
                Text enthält
                <input
                  value={form.trigger.filter_text_contains}
                  onChange={(event) =>
                    patchTrigger({ filter_text_contains: event.target.value })
                  }
                  placeholder="Rechnung"
                  disabled={!canEdit}
                />
              </label>
              <label>
                Text-Regex
                <input
                  value={form.trigger.filter_text_regex}
                  onChange={(event) =>
                    patchTrigger({ filter_text_regex: event.target.value })
                  }
                  placeholder="SR-\\d+"
                  disabled={!canEdit}
                />
              </label>
            </div>
            <div className="workflow-tag-grid">
              <fieldset>
                <legend>Muss Tags haben</legend>
                <div className="workflow-chip-row">
                  {refs.tags.map((tag) => (
                    <label className="workflow-chip" key={tag.id}>
                      <input
                        type="checkbox"
                        checked={form.trigger.filter_has_tags.includes(tag.id)}
                        onChange={() =>
                          toggleTag(
                            form.trigger.filter_has_tags,
                            (next) => patchTrigger({ filter_has_tags: next }),
                            tag.id,
                          )
                        }
                        disabled={!canEdit}
                      />
                      {tag.name}
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset>
                <legend>Darf Tags nicht haben</legend>
                <div className="workflow-chip-row">
                  {refs.tags.map((tag) => (
                    <label className="workflow-chip" key={tag.id}>
                      <input
                        type="checkbox"
                        checked={form.trigger.filter_has_not_tags.includes(tag.id)}
                        onChange={() =>
                          toggleTag(
                            form.trigger.filter_has_not_tags,
                            (next) => patchTrigger({ filter_has_not_tags: next }),
                            tag.id,
                          )
                        }
                        disabled={!canEdit}
                      />
                      {tag.name}
                    </label>
                  ))}
                </div>
              </fieldset>
            </div>
          </section>

          <section className="workflow-panel">
            <div className="workflow-section-head">
              <span className="workflow-step">3</span>
              <div>
                <h3>Dann</h3>
                <p>Aktionen laufen in der angezeigten Reihenfolge.</p>
              </div>
            </div>
            <div className="workflow-actions-stack">
              {form.actions.map((action, index) => (
                <article className="workflow-action-card" key={index}>
                  <div className="workflow-action-card__head">
                    <strong>Aktion {index + 1}</strong>
                    <select
                      value={action.action_type}
                      onChange={(event) =>
                        patchAction(index, {
                          action_type: event.target.value as WorkflowActionType,
                        })
                      }
                      disabled={!canEdit}
                    >
                      <option value="assign">Zuweisen</option>
                      <option value="remove">Entfernen</option>
                    </select>
                    {canEdit && form.actions.length > 1 && (
                      <button type="button" className="link" onClick={() => removeAction(index)}>
                        Entfernen
                      </button>
                    )}
                  </div>

                  {action.action_type === "assign" ? (
                    <>
                      <div className="workflow-form-grid">
                        <label>
                          Titel-Template
                          <input
                            value={action.assign_title}
                            onChange={(event) =>
                              patchAction(index, { assign_title: event.target.value })
                            }
                            placeholder="{correspondent} - {doc_type} {created}"
                            disabled={!canEdit}
                          />
                        </label>
                        <label>
                          Korrespondent
                          <select
                            value={action.assign_correspondent ?? ""}
                            onChange={(event) =>
                              patchAction(index, {
                                assign_correspondent: event.target.value
                                  ? Number(event.target.value)
                                  : null,
                              })
                            }
                            disabled={!canEdit}
                          >
                            <option value="">nicht setzen</option>
                            {refs.correspondents.map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          Dokumenttyp
                          <select
                            value={action.assign_document_type ?? ""}
                            onChange={(event) =>
                              patchAction(index, {
                                assign_document_type: event.target.value
                                  ? Number(event.target.value)
                                  : null,
                              })
                            }
                            disabled={!canEdit}
                          >
                            <option value="">nicht setzen</option>
                            {refs.docTypes.map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          Ablagepfad
                          <select
                            value={action.assign_storage_path ?? ""}
                            onChange={(event) =>
                              patchAction(index, {
                                assign_storage_path: event.target.value
                                  ? Number(event.target.value)
                                  : null,
                              })
                            }
                            disabled={!canEdit}
                          >
                            <option value="">nicht setzen</option>
                            {refs.storagePaths.map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.name}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                      <fieldset>
                        <legend>Tags hinzufügen</legend>
                        <div className="workflow-chip-row">
                          {refs.tags.map((tag) => (
                            <label className="workflow-chip" key={tag.id}>
                              <input
                                type="checkbox"
                                checked={action.assign_tags.includes(tag.id)}
                                onChange={() =>
                                  toggleTag(
                                    action.assign_tags,
                                    (next) => patchAction(index, { assign_tags: next }),
                                    tag.id,
                                  )
                                }
                                disabled={!canEdit}
                              />
                              {tag.name}
                            </label>
                          ))}
                        </div>
                      </fieldset>
                    </>
                  ) : (
                    <fieldset>
                      <legend>Tags entfernen</legend>
                      <div className="workflow-chip-row">
                        {refs.tags.map((tag) => (
                          <label className="workflow-chip" key={tag.id}>
                            <input
                              type="checkbox"
                              checked={action.remove_tags.includes(tag.id)}
                              onChange={() =>
                                toggleTag(
                                  action.remove_tags,
                                  (next) => patchAction(index, { remove_tags: next }),
                                  tag.id,
                                )
                              }
                              disabled={!canEdit}
                            />
                            {tag.name}
                          </label>
                        ))}
                      </div>
                    </fieldset>
                  )}
                </article>
              ))}
            </div>
            {canEdit && (
              <button type="button" className="link workflow-add-action" onClick={addAction}>
                Aktion hinzufügen
              </button>
            )}
          </section>

          <section className="workflow-preview">
            <div className="workflow-section-head">
              <span className="workflow-step">4</span>
              <div>
                <h3>Vorschau</h3>
                <p>So liest sich der Workflow in normaler Sprache.</p>
              </div>
            </div>
            <div className="workflow-flow">
              <div className="workflow-flow__node">
                <span>Wenn</span>
                <strong>{triggerParts[0]}</strong>
                {triggerParts.slice(1).map((part) => (
                  <small key={part}>{part}</small>
                ))}
              </div>
              <div className="workflow-flow__arrow" aria-hidden="true">
                →
              </div>
              <div className="workflow-flow__node">
                <span>Dann</span>
                {form.actions.map((action, index) => (
                  <strong key={`${action.action_type}-${index}`}>
                    {index + 1}. {actionSummary(action, refs)}
                  </strong>
                ))}
              </div>
            </div>
          </section>

          {saveError && (
            <div className="state state--error">
              <strong>Workflow konnte nicht gespeichert werden.</strong>
              <span>{saveError}</span>
            </div>
          )}

          {canEdit && (
            <div className="workflow-savebar">
              <button type="button" onClick={save} disabled={saving || !canSave}>
                {saving ? "Speichere ..." : form.id ? "Workflow speichern" : "Workflow anlegen"}
              </button>
              <button type="button" className="link" onClick={newWorkflow}>
                Zurücksetzen
              </button>
            </div>
          )}
        </main>
      </div>
    </section>
  );
}
