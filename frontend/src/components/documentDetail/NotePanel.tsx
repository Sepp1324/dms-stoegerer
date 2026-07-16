import { useState } from "react";

import { updateDocument, type DocumentDetail } from "../../api";

/**
 * Freie persönliche Notiz zum Dokument (z. B. „Kündigung abgeschickt am …").
 * Inline bearbeitbar für den Eigentümer; für andere (bei geteilten Dokumenten)
 * read-only, falls eine Notiz vorhanden ist. Die Notiz ist durchsuchbar.
 */
export function NotePanel({
  documentId,
  initialNote,
  editable,
  onSaved,
}: {
  documentId: number;
  initialNote: string;
  editable: boolean;
  onSaved: (doc: DocumentDetail) => void;
}) {
  const [note, setNote] = useState(initialNote);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = note !== initialNote;

  if (!editable) {
    if (!initialNote.trim()) return null;
    return (
      <section className="card note-panel">
        <h3>Notiz</h3>
        <p className="note-panel__readonly">{initialNote}</p>
      </section>
    );
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      onSaved(await updateDocument(documentId, { note }));
    } catch {
      setError("Notiz konnte nicht gespeichert werden.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card note-panel">
      <h3>Notiz</h3>
      <textarea
        className="note-panel__input"
        rows={3}
        placeholder="Freie Notiz – z. B. Kündigung abgeschickt am 15.01.2026 (durchsuchbar)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      {error && <p className="form-error">{error}</p>}
      <div className="note-panel__actions">
        <button onClick={save} disabled={saving || !dirty}>
          {saving ? "Speichere …" : "Notiz speichern"}
        </button>
        {dirty && !saving && (
          <button className="link" onClick={() => setNote(initialNote)}>
            Verwerfen
          </button>
        )}
      </div>
    </section>
  );
}
