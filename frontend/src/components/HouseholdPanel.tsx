import { useEffect, useState } from "react";

import {
  clearHouseholdJoinCode,
  createHousehold,
  decideHouseholdJoinRequest,
  generateHouseholdJoinCode,
  getMyHousehold,
  leaveHousehold,
  listHouseholdJoinRequests,
  requestHouseholdJoin,
  type Household,
  type HouseholdJoinRequest,
} from "../api";

/**
 * Familien-/Haushaltsverwaltung. Beitritt NUR mit beidseitiger Zustimmung:
 * Der Owner (einziger Admin) teilt einen Beitritts-Code; ein Interessent stellt
 * damit eine Beitrittsanfrage, die der Owner bestätigt. Erst die Bestätigung
 * erzeugt die Mitgliedschaft (und damit die gegenseitige Dokument-Sichtbarkeit).
 * Ein Nutzer ist in höchstens einem Haushalt.
 */
export default function HouseholdPanel() {
  const [household, setHousehold] = useState<Household | null>(null);
  const [requests, setRequests] = useState<HouseholdJoinRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const hh = await getMyHousehold();
      setHousehold(hh);
      if (hh?.is_owner) {
        setRequests(await listHouseholdJoinRequests(hh.id));
      } else {
        setRequests([]);
      }
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
    setInfo(null);
    try {
      await createHousehold(name.trim());
      setName("");
      await load();
    } catch {
      setError("Anlegen fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }

  async function handleJoin(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim()) return;
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      await requestHouseholdJoin(code.trim());
      setCode("");
      setInfo("Beitrittsanfrage gestellt. Ein Haushalts-Admin muss sie bestätigen.");
    } catch {
      setError("Beitritt fehlgeschlagen (Code ungültig oder du bist bereits in einem Haushalt).");
    } finally {
      setBusy(false);
    }
  }

  async function handleGenerateCode() {
    if (!household) return;
    setBusy(true);
    setError(null);
    try {
      setHousehold(await generateHouseholdJoinCode(household.id));
    } catch {
      setError("Code konnte nicht erzeugt werden.");
    } finally {
      setBusy(false);
    }
  }

  async function handleClearCode() {
    if (!household) return;
    setBusy(true);
    setError(null);
    try {
      setHousehold(await clearHouseholdJoinCode(household.id));
    } catch {
      setError("Code konnte nicht gelöscht werden.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDecide(requestId: number, decision: "approve" | "reject") {
    if (!household) return;
    setBusy(true);
    setError(null);
    try {
      setHousehold(await decideHouseholdJoinRequest(household.id, requestId, decision));
      setRequests(await listHouseholdJoinRequests(household.id));
    } catch {
      setError("Entscheidung fehlgeschlagen.");
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
        Lesen). Der Beitritt braucht beidseitige Zustimmung: Der Admin teilt einen
        Code, du stellst damit eine Anfrage, der Admin bestätigt.
      </p>

      {loading && <p className="muted">Lade …</p>}
      {error && <p className="form-error">{error}</p>}
      {info && <p className="muted">{info}</p>}

      {!loading && !household && (
        <>
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
          <form className="household__form" onSubmit={handleJoin}>
            <p className="muted">… oder einem bestehenden per Code beitreten:</p>
            <div className="household__row">
              <input
                placeholder="Beitritts-Code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
              <button type="submit" disabled={busy || !code.trim()}>
                Beitritt anfragen
              </button>
            </div>
          </form>
        </>
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
                {household.owner && household.owner.id === member.id ? (
                  <span className="muted"> · Admin</span>
                ) : null}
                {member.email ? <span className="muted"> · {member.email}</span> : null}
              </li>
            ))}
          </ul>

          {household.is_owner && (
            <>
              <div className="household__code">
                <p className="muted">
                  Beitritts-Code{" "}
                  {household.join_code ? (
                    <code>{household.join_code}</code>
                  ) : (
                    <span>– noch keiner erzeugt</span>
                  )}
                </p>
                <div className="household__row">
                  <button onClick={handleGenerateCode} disabled={busy}>
                    {household.join_code ? "Neuen Code erzeugen" : "Code erzeugen"}
                  </button>
                  {household.join_code && (
                    <button className="link" onClick={handleClearCode} disabled={busy}>
                      Code löschen
                    </button>
                  )}
                </div>
              </div>

              {requests.length > 0 && (
                <div className="household__requests">
                  <p className="muted">Offene Beitrittsanfragen:</p>
                  <ul className="household__members">
                    {requests.map((req) => (
                      <li key={req.id}>
                        {req.user.username}
                        <button
                          className="link"
                          onClick={() => handleDecide(req.id, "approve")}
                          disabled={busy}
                        >
                          Bestätigen
                        </button>
                        <button
                          className="link"
                          onClick={() => handleDecide(req.id, "reject")}
                          disabled={busy}
                        >
                          Ablehnen
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
