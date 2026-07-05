import { useState } from "react";
import type { CustomField, CustomFieldValue } from "../../api";
import {
  formatCustomFieldValue,
  toCanonicalValue,
  toInputValue,
} from "../../customFields";

// Zusatzfelder-Sektion (STOAA-113): zeigt ALLE Feld-Definitionen (auch ohne
// Wert → „—") und erlaubt Inline-Bearbeitung bei Schreibrecht. Typkorrekte
// Anzeige (NUMBER deutsch, DATE DD.MM.YYYY, BOOLEAN Ja/Nein) und passende
// Eingabe-Elemente pro Datentyp.
export function CustomFieldsPanel({
  fields,
  values,
  canEdit,
  onSave,
  onManageFields,
}: {
  fields: CustomField[];
  values: CustomFieldValue[];
  canEdit: boolean;
  onSave: (values: { field: number; value: string }[]) => Promise<void>;
  onManageFields?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<number, string>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<number, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Aktuellen kanonischen Wert je Feld nachschlagbar machen.
  const valueByField = new Map(values.map((v) => [v.field, v.value]));

  function startEdit() {
    const next: Record<number, string> = {};
    for (const f of fields) {
      next[f.id] = toInputValue(valueByField.get(f.id) ?? "", f.data_type);
    }
    setDraft(next);
    setFieldErrors({});
    setSaveError(null);
    setEditing(true);
  }

  async function save() {
    // Alle Felder validieren + in kanonische Werte konvertieren.
    const out: { field: number; value: string }[] = [];
    const errs: Record<number, string> = {};
    for (const f of fields) {
      const res = toCanonicalValue(draft[f.id] ?? "", f.data_type);
      if (res.error) {
        errs[f.id] = res.error;
      } else {
        out.push({ field: f.id, value: res.value ?? "" });
      }
    }
    if (Object.keys(errs).length > 0) {
      setFieldErrors(errs);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await onSave(out);
      setEditing(false);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  // Ohne Definitionen: dezenter Empty-State mit Verwaltungs-Link (bei Schreibrecht).
  if (fields.length === 0) {
    return (
      <div className="custom-fields">
        <div className="custom-fields__head">
          <h3>Zusatzfelder</h3>
        </div>
        <div className="state-block state-block--subtle">
          <p className="state-block__detail">Keine Zusatzfelder definiert</p>
          {canEdit && onManageFields && (
            <button
              className="link custom-fields__manage"
              onClick={onManageFields}
            >
              Felder verwalten
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="custom-fields">
      <div className="custom-fields__head">
        <h3>Zusatzfelder</h3>
        {canEdit && !editing && (
          <button
            className="link custom-fields__edit"
            onClick={startEdit}
            aria-label="Zusatzfelder bearbeiten"
            title="Zusatzfelder bearbeiten"
          >
            ✎
          </button>
        )}
      </div>

      {editing ? (
        <div className="custom-fields__form">
          {fields.map((f) => (
            <CustomFieldInput
              key={f.id}
              field={f}
              value={draft[f.id] ?? ""}
              error={fieldErrors[f.id]}
              onChange={(v) =>
                setDraft((d) => ({ ...d, [f.id]: v }))
              }
            />
          ))}
          {saveError && (
            <p className="status status--error" role="status">
              {saveError}
            </p>
          )}
          <div className="edit-actions">
            <button onClick={save} disabled={saving}>
              {saving ? "Speichern …" : "Speichern"}
            </button>
            <button
              className="link"
              onClick={() => setEditing(false)}
              disabled={saving}
            >
              Abbrechen
            </button>
          </div>
        </div>
      ) : (
        <dl className="custom-fields__list">
          {fields.map((f) => {
            const raw = valueByField.get(f.id) ?? "";
            const empty = raw === "";
            return (
              <div key={f.id} className="custom-fields__row">
                <dt>{f.name}</dt>
                <dd className={empty ? "muted" : undefined}>
                  {formatCustomFieldValue(raw, f.data_type)}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
    </div>
  );
}

// Einzelnes Edit-Input für ein Zusatzfeld – Element passend zum Datentyp.
function CustomFieldInput({
  field,
  value,
  error,
  onChange,
}: {
  field: CustomField;
  value: string;
  error?: string;
  onChange: (v: string) => void;
}) {
  const inputId = `cf-${field.id}`;
  return (
    <label className="custom-fields__field" htmlFor={inputId}>
      <span className="custom-fields__label">{field.name}</span>
      {field.data_type === "boolean" ? (
        <select
          id={inputId}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">—</option>
          <option value="true">Ja</option>
          <option value="false">Nein</option>
        </select>
      ) : field.data_type === "date" ? (
        <input
          id={inputId}
          type="date"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : field.data_type === "currency" ? (
        <span className="input-with-suffix">
          <input
            id={inputId}
            type="text"
            inputMode="decimal"
            placeholder="z. B. 1234,56"
            value={value}
            onChange={(e) => onChange(e.target.value)}
          />
          <span className="suffix">€</span>
        </span>
      ) : field.data_type === "number" ? (
        <input
          id={inputId}
          type="text"
          inputMode="decimal"
          placeholder="z. B. 1234,56"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input
          id={inputId}
          type="text"
          placeholder="z. B. Vertragsnummer"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {error && (
        <span className="input-error status--error" role="status">
          {error}
        </span>
      )}
    </label>
  );
}
