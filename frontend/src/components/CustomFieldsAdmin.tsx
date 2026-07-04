import { useEffect, useState } from "react";
import {
  createCustomField,
  deleteCustomField,
  getCustomFields,
  updateCustomField,
  type CustomField,
  type CustomFieldDataType,
} from "../api";
import { DATA_TYPE_LABELS, DATA_TYPE_OPTIONS } from "../customFields";

// SPA-Verwaltung der Zusatzfeld-Definitionen (Spec §3 Stufe 2, STOAA-113).
// Anlegen (Name + Typ), Umbenennen (Typ ist read-only) und Löschen (Backend
// verhindert Löschen bei vorhandenen Werten → 409, hier abgefangen).
export default function CustomFieldsAdmin({
  canEdit,
  onChanged,
}: {
  canEdit: boolean;
  onChanged?: () => void;
}) {
  const [fields, setFields] = useState<CustomField[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Anlege-Formular
  const [name, setName] = useState("");
  const [dataType, setDataType] = useState<CustomFieldDataType>("text");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    getCustomFields()
      .then((f) => {
        setFields(f);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  // Nach Mutationen: eigene Liste + übergeordnete Definitionen aktualisieren.
  function refresh() {
    load();
    onChanged?.();
  }

  async function create() {
    if (!name.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      await createCustomField(name.trim(), dataType);
      setName("");
      setDataType("text");
      refresh();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fields-view">
      <p className="muted" style={{ marginTop: 0 }}>
        Zusatzfelder sind typisierte Attribute an Dokumenten (z. B.
        Rechnungsbetrag, Fälligkeitsdatum). Der Datentyp ist nach dem Anlegen
        nicht mehr änderbar; Felder mit vorhandenen Werten lassen sich nicht
        löschen.
      </p>

      {canEdit && (
        <section className="card field-form">
          <h3 style={{ marginTop: 0 }}>Neues Feld anlegen</h3>
          <div className="field-form__grid">
            <label>
              Feldname
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="z. B. Rechnungsbetrag"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && name.trim()) {
                    e.preventDefault();
                    create();
                  }
                }}
              />
            </label>
            <label>
              Datentyp
              <select
                value={dataType}
                onChange={(e) =>
                  setDataType(e.target.value as CustomFieldDataType)
                }
              >
                {DATA_TYPE_OPTIONS.map((dt) => (
                  <option key={dt} value={dt}>
                    {DATA_TYPE_LABELS[dt]}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {saveError && <p className="status status--error">{saveError}</p>}
          <button onClick={create} disabled={saving || !name.trim()}>
            {saving ? "Anlegen …" : "Feld anlegen"}
          </button>
        </section>
      )}

      {loading && <p className="muted" role="status">Lade …</p>}
      {error && (
        <div className="state-block state-block--error">
          <p className="state-block__title">
            Zusatzfelder konnten nicht geladen werden
          </p>
          <p className="state-block__detail">{error}</p>
          <div className="state-block__action">
            <button onClick={load}>Erneut versuchen</button>
          </div>
        </div>
      )}
      {!loading && !error && fields.length === 0 && (
        <div className="state-block state-block--subtle">
          <p className="state-block__title">Noch keine Felder angelegt</p>
          <p className="state-block__detail">
            Lege oben ein erstes Zusatzfeld an.
          </p>
        </div>
      )}

      {fields.length > 0 && (
        <ul className="field-list">
          {fields.map((f) => (
            <FieldRow
              key={f.id}
              field={f}
              canEdit={canEdit}
              onChanged={refresh}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

// Eine Zeile der Feldliste – Anzeige, Inline-Umbenennen und Löschen.
function FieldRow({
  field,
  canEdit,
  onChanged,
}: {
  field: CustomField;
  canEdit: boolean;
  onChanged: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(field.name);
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);
  // Löschbestätigung inline (keine window.confirm-Dialoge im dunklen Theme).
  const [confirmDelete, setConfirmDelete] = useState(false);

  async function saveName() {
    if (!name.trim() || name.trim() === field.name) {
      setRenaming(false);
      setName(field.name);
      return;
    }
    setBusy(true);
    setRowError(null);
    try {
      await updateCustomField(field.id, name.trim());
      setRenaming(false);
      onChanged();
    } catch (e) {
      setRowError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setRowError(null);
    try {
      await deleteCustomField(field.id);
      onChanged();
    } catch (e) {
      // 409 = Feld ist noch in Dokumenten verwendet.
      setRowError(
        e instanceof Error
          ? e.message
          : "Löschen fehlgeschlagen – Feld wird noch verwendet.",
      );
      setConfirmDelete(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="card field-item">
      <div className="field-item__head">
        {renaming ? (
          <input
            className="field-item__rename"
            value={name}
            aria-label="Feldname"
            autoFocus
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") saveName();
              if (e.key === "Escape") {
                setRenaming(false);
                setName(field.name);
              }
            }}
          />
        ) : (
          <span className="field-item__name">{field.name}</span>
        )}
        <span className="field-item__type muted">
          {DATA_TYPE_LABELS[field.data_type]}
        </span>

        {canEdit && (
          <div className="field-item__actions">
            {renaming ? (
              <>
                <button className="link" onClick={saveName} disabled={busy}>
                  {busy ? "…" : "Speichern"}
                </button>
                <button
                  className="link"
                  onClick={() => {
                    setRenaming(false);
                    setName(field.name);
                  }}
                  disabled={busy}
                >
                  Abbrechen
                </button>
              </>
            ) : confirmDelete ? (
              <>
                <span className="muted">Löschen?</span>
                <button
                  className="link field-item__delete"
                  onClick={remove}
                  disabled={busy}
                >
                  {busy ? "…" : "Ja, löschen"}
                </button>
                <button
                  className="link"
                  onClick={() => setConfirmDelete(false)}
                  disabled={busy}
                >
                  Abbrechen
                </button>
              </>
            ) : (
              <>
                <button className="link" onClick={() => setRenaming(true)}>
                  Bearbeiten
                </button>
                <button
                  className="link field-item__delete"
                  onClick={() => setConfirmDelete(true)}
                  aria-label={`Feld ${field.name} löschen`}
                >
                  Löschen
                </button>
              </>
            )}
          </div>
        )}
      </div>
      {rowError && <p className="status status--error">{rowError}</p>}
    </li>
  );
}
