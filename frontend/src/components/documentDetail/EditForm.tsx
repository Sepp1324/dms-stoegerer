import type { Dispatch, SetStateAction } from "react";
import type { NamedRef } from "../../api";
import { CreatableSelect } from "./CreatableSelect";
import { InlineCreate } from "./InlineCreate";

// Formzustand des Metadaten-Edit-Formulars (Übersicht-Tab). Wird vom
// Orchestrator (DocumentDetail) gehalten und hier bearbeitet.
export interface EditFormState {
  title: string;
  correspondent: number | "";
  document_type: number | "";
  storage_path: number | "";
  tagIds: Set<number>;
}

// Metadaten-Edit-Formular des Übersicht-Tabs (Titel, Klassifizierung, Tags). Aus
// dem Haupt-Render von DocumentDetail.tsx extrahiert (STOAA-431) – Verhalten
// unverändert. State + Persistenz liegen weiterhin im Orchestrator.
export function EditForm({
  form,
  setForm,
  correspondents,
  documentTypes,
  storagePaths,
  allTags,
  onCreateCorrespondent,
  onCreateDocumentType,
  onCreateStoragePath,
  onCreateTag,
  toggleTag,
  saving,
  saveError,
  onSave,
  onCancel,
}: {
  form: EditFormState;
  setForm: Dispatch<SetStateAction<EditFormState>>;
  correspondents: NamedRef[];
  documentTypes: NamedRef[];
  storagePaths: NamedRef[];
  allTags: NamedRef[];
  onCreateCorrespondent: (name: string) => Promise<NamedRef>;
  onCreateDocumentType: (name: string) => Promise<NamedRef>;
  onCreateStoragePath: (name: string) => Promise<NamedRef>;
  onCreateTag: (name: string) => Promise<NamedRef>;
  toggleTag: (tagId: number) => void;
  saving: boolean;
  saveError: string | null;
  onSave: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="edit-form">
      <label>
        Titel
        <input
          value={form.title}
          onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
        />
      </label>

      <CreatableSelect
        label="Korrespondent"
        value={form.correspondent}
        onChange={(v) => setForm((f) => ({ ...f, correspondent: v }))}
        options={correspondents}
        onCreate={onCreateCorrespondent}
      />
      <CreatableSelect
        label="Typ"
        value={form.document_type}
        onChange={(v) => setForm((f) => ({ ...f, document_type: v }))}
        options={documentTypes}
        onCreate={onCreateDocumentType}
      />
      <CreatableSelect
        label="Ablagepfad"
        value={form.storage_path}
        onChange={(v) => setForm((f) => ({ ...f, storage_path: v }))}
        options={storagePaths}
        onCreate={onCreateStoragePath}
      />

      <div className="edit-tags">
        <span className="edit-tags__label">Schlagworte</span>
        <div className="tag-toggle-list">
          {allTags.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`tag tag-toggle ${form.tagIds.has(t.id) ? "tag-toggle--on" : ""}`}
              onClick={() => toggleTag(t.id)}
            >
              {t.name}
            </button>
          ))}
        </div>
        <InlineCreate
          placeholder="Neues Schlagwort"
          buttonLabel="+ Tag"
          onCreate={async (name) => {
            const item = await onCreateTag(name);
            toggleTag(item.id);
          }}
        />
      </div>

      {saveError && <p className="status status--error">{saveError}</p>}
      <div className="edit-actions">
        <button onClick={onSave} disabled={saving || !form.title.trim()}>
          {saving ? "Speichern …" : "Speichern"}
        </button>
        <button className="link" onClick={onCancel} disabled={saving}>
          Abbrechen
        </button>
      </div>
    </div>
  );
}
