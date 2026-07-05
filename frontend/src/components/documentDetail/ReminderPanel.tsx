import { useEffect, useState } from "react";
import {
  createReminder,
  deleteReminder,
  listReminders,
  markReminderDone,
  type Reminder,
} from "../../api";
import { formatDateOnly } from "./format";

// Wiedervorlage-Sektion (STOAA-372/374): Erinnerungen je Dokument. Schreibende
// Nutzer legen Datum + optionale Notiz an, sehen die Liste bestehender
// Erinnerungen mit Status und können sie als erledigt markieren oder löschen.
// Gäste (kein ``canEdit``) sehen die Liste nur lesend (Anlegen/Aktionen aus).
export function ReminderPanel({
  documentId,
  canEdit,
}: {
  documentId: number;
  canEdit: boolean;
}) {
  const [reminders, setReminders] = useState<Reminder[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [remindOn, setRemindOn] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    setReminders(null);
    setLoadError(null);
    listReminders(documentId)
      .then((rows) => active && setReminders(rows))
      .catch((e) => active && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
  }, [documentId]);

  async function create() {
    if (!remindOn) return;
    setSaving(true);
    setSaveError(null);
    try {
      const created = await createReminder({
        document: documentId,
        remind_on: remindOn,
        note: note.trim(),
      });
      setReminders((prev) => sortReminders([created, ...(prev ?? [])]));
      setRemindOn("");
      setNote("");
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function complete(id: number) {
    setBusyId(id);
    setSaveError(null);
    try {
      const updated = await markReminderDone(id);
      setReminders((prev) => prev?.map((r) => (r.id === id ? updated : r)) ?? null);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(id: number) {
    setBusyId(id);
    setSaveError(null);
    try {
      await deleteReminder(id);
      setReminders((prev) => prev?.filter((r) => r.id !== id) ?? null);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="reminders">
      <div className="reminders__head">
        <h3>Wiedervorlage</h3>
      </div>

      {canEdit && (
        <div className="reminders__form">
          <label className="reminders__field">
            <span className="muted">Fällig am</span>
            <input
              type="date"
              value={remindOn}
              onChange={(e) => setRemindOn(e.target.value)}
            />
          </label>
          <label className="reminders__field reminders__field--grow">
            <span className="muted">Notiz (optional)</span>
            <input
              type="text"
              value={note}
              placeholder="z. B. Frist prüfen"
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <button onClick={create} disabled={saving || !remindOn}>
            {saving ? "Anlegen …" : "Erinnerung anlegen"}
          </button>
        </div>
      )}

      {saveError && <p className="status status--error">{saveError}</p>}
      {loadError && <p className="status status--error">{loadError}</p>}
      {reminders === null && !loadError && <p className="muted">Lade …</p>}
      {reminders && reminders.length === 0 && (
        <p className="muted reminders__empty">Keine Wiedervorlagen.</p>
      )}
      {reminders && reminders.length > 0 && (
        <ul className="reminders__list">
          {reminders.map((r) => (
            <li key={r.id} className="reminders__row">
              <span
                className={`reminder-badge reminder-badge--${r.done ? "done" : "open"}`}
              >
                {r.done ? "Erledigt" : "Offen"}
              </span>
              <span className="reminders__date">{formatDateOnly(r.remind_on)}</span>
              <span className="reminders__note">{r.note || <em className="muted">—</em>}</span>
              {canEdit && (
                <span className="reminders__actions">
                  {!r.done && (
                    <button
                      className="link"
                      onClick={() => complete(r.id)}
                      disabled={busyId === r.id}
                    >
                      erledigt
                    </button>
                  )}
                  <button
                    className="link reminders__delete"
                    onClick={() => remove(r.id)}
                    disabled={busyId === r.id}
                  >
                    löschen
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Erinnerungen nach Fälligkeitsdatum aufsteigend – identisch zur Backend-
// Ordnung (``ordering = ["remind_on"]``), damit ein neu angelegter Eintrag an
// der richtigen Stelle einsortiert wird.
function sortReminders(rows: Reminder[]): Reminder[] {
  return [...rows].sort((a, b) => a.remind_on.localeCompare(b.remind_on));
}
