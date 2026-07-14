import { useEffect, useState } from "react";

import {
  addHouseholdMember,
  createHousehold,
  getMyHousehold,
  leaveHousehold,
  type Household,
} from "../api";

/**
 * Familien-/Haushaltsverwaltung: Haushalt anlegen, Mitglieder (per Benutzername)
 * hinzufügen, verlassen. Grundlage der Dokument-Freigabe an die Familie – ein
 * Nutzer ist in höchstens einem Haushalt.
 */
export default function HouseholdPanel() {
  const [household, setHousehold] = useState<Household | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setHousehold(await getMyHousehold());
    } catch {
      setError("Haushalt konnte nicht geladen werden.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setHousehold(await createHousehold(name.trim()));
      setName("");
    } catch {
      setError("Anlegen fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!household || !username.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setHousehold(await addHouseholdMember(household.id, username.trim()));
      setUsername("");
    } catch {
      setError(`„${username.trim()}" konnte nicht hinzugefügt werden (unbekannt oder schon in einem Haushalt).`);
    } finally {
      setBusy(false);
    }
  }

  async function handleLeave() {
    if (!household) return;
    setBusy(true);
    setError(null);
    try {
      await leaveHousehold(household.id);
      await load();
    } catch {
      setError("Verlassen fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card household">
      <h3>Familie / Haushalt</h3>
      <p className="muted household__hint">
        Mitglieder eines Haushalts können Dokumente füreinander freigeben (nur
        Lesen). Freigegebene Dokumente erscheinen in der Liste der anderen Mitglieder.
      </p>

      {loading && <p className="muted">Lade …</p>}
      {error && <p className="form-error">{error}</p>}

      {!loading && !household && (
        <form className="household__form" onSubmit={handleCreate}>
          <p className="muted">Du bist in keinem Haushalt. Leg einen an:</p>
          <div className="household__row">
            <input
              placeholder="Name des Haushalts (z. B. Familie Muster)"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button type="submit" disabled={busy || !name.trim()}>
              Haushalt anlegen
            </button>
          </div>
        </form>
      )}

      {!loading && household && (
        <>
          <div className="household__head">
            <strong>{household.name}</strong>
            <button className="link" onClick={handleLeave} disabled={busy}>
              Verlassen
            </button>
          </div>
          <ul className="household__members">
            {household.members.map((member) => (
              <li key={member.id}>
                {member.username}
                {member.email ? <span className="muted"> · {member.email}</span> : null}
              </li>
            ))}
          </ul>
          <form className="household__form" onSubmit={handleAdd}>
            <div className="household__row">
              <input
                placeholder="Benutzername hinzufügen"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
              <button type="submit" disabled={busy || !username.trim()}>
                Hinzufügen
              </button>
            </div>
          </form>
        </>
      )}
    </section>
  );
}
