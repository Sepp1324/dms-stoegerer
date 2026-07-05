import { useEffect, useState } from "react";
import {
  createMailAccount,
  deleteMailAccount,
  getMailAccounts,
  getUsers,
  testMailAccount,
  updateMailAccount,
  type MailAccount,
  type MailAccountPayload,
  type MailTestResult,
  type User,
} from "../api";

// SPA-Verwaltung der IMAP-Postfächer (STOAA-215, Backend STOAA-214). Nur für
// DMS-Admins erreichbar (der Menüpunkt wird sonst nicht gezeigt; das Backend
// schützt zusätzlich mit 403). Vorbild: CustomFieldsAdmin.tsx / RulesPage.tsx —
// hand-gerollt, plain fetch über src/api.ts, keine UI-Libs.

// Formular-Entwurf: alle Felder als Strings/Bools für die Eingabe; Port und
// Eigentümer-ID werden erst beim Absenden validiert und konvertiert.
interface Draft {
  name: string;
  host: string;
  port: string;
  use_ssl: boolean;
  username: string;
  password: string;
  folder: string;
  owner: string; // Nutzer-ID als Text; leer = Admin-Triage (owner=null)
  enabled: boolean;
}

const EMPTY_DRAFT: Draft = {
  name: "",
  host: "",
  port: "993",
  use_ssl: true,
  username: "",
  password: "",
  folder: "INBOX",
  owner: "",
  enabled: true,
};

function draftFromAccount(a: MailAccount): Draft {
  return {
    name: a.name,
    host: a.host,
    port: String(a.port),
    use_ssl: a.use_ssl,
    username: a.username,
    password: "", // nie vorbelegen; leer = unverändert (Backend-Verhalten)
    folder: a.folder,
    owner: a.owner == null ? "" : String(a.owner),
    enabled: a.enabled,
  };
}

// Anzeige-Label des Eigentümers: Benutzername, falls in der Nutzerliste
// gefunden, sonst Fallback auf die ID (Nutzer könnte deaktiviert/gelöscht sein).
function ownerLabel(owner: number | null, users: User[]): string {
  if (owner == null) return "— (Admin-Triage)";
  const u = users.find((x) => x.id === owner);
  return u ? u.username : `Nutzer #${owner}`;
}

// Hand-gerollte Validierung (kein Zod): host/username/name nicht leer, Port
// 1–65535, optionale Eigentümer-ID positiv, Ordner-Default INBOX. Gibt bei
// Erfolg die fertige Payload zurück, sonst eine deutsche Fehlermeldung.
function validate(
  d: Draft,
  isEdit: boolean,
): { error: string | null; payload?: MailAccountPayload } {
  if (!d.name.trim()) return { error: "Bitte einen Namen angeben." };
  if (!d.host.trim()) return { error: "Bitte einen Server (Host) angeben." };
  if (!d.username.trim()) return { error: "Bitte einen Benutzernamen angeben." };
  const port = Number(d.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    return { error: "Port muss eine ganze Zahl zwischen 1 und 65535 sein." };
  let owner: number | null = null;
  if (d.owner.trim()) {
    const o = Number(d.owner);
    if (!Number.isInteger(o) || o < 1)
      return {
        error:
          "Eigentümer-ID muss eine positive Zahl sein (leer lassen = Admin-Triage).",
      };
    owner = o;
  }
  const payload: MailAccountPayload = {
    name: d.name.trim(),
    owner,
    host: d.host.trim(),
    port,
    use_ssl: d.use_ssl,
    username: d.username.trim(),
    folder: d.folder.trim() || "INBOX",
    enabled: d.enabled,
  };
  // Leeres Passwort beim Bearbeiten = unverändert → gar nicht mitschicken.
  // Beim Anlegen mitschicken (auch leer erlaubt: Passwort kann via Secret-Env
  // kommen, das Backend akzeptiert allow_blank).
  if (d.password || !isEdit) payload.password = d.password;
  return { error: null, payload };
}

export default function MailAccountsAdmin({ canEdit }: { canEdit: boolean }) {
  const [accounts, setAccounts] = useState<MailAccount[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  function load() {
    setLoading(true);
    getMailAccounts()
      .then((a) => {
        setAccounts(a);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);
  // Nutzerliste einmalig laden (nur zur Owner-Zuordnung). Ein Fehler hier ist
  // nicht kritisch – das Dropdown fällt dann auf „nur Admin-Triage" zurück.
  useEffect(() => {
    getUsers()
      .then(setUsers)
      .catch(() => setUsers([]));
  }, []);

  return (
    <div className="fields-view">
      <p className="muted" style={{ marginTop: 0 }}>
        Mailkonten sind IMAP-Postfächer, deren Anhänge automatisch ins DMS
        importiert werden. Das Passwort wird nur gespeichert, niemals angezeigt.
        Ohne Eigentümer landen eingehende Dokumente im Admin-Triage-Postfach.
      </p>

      {canEdit &&
        (adding ? (
          <MailAccountForm
            title="Neues Mailkonto"
            submitLabel="Konto anlegen"
            initial={EMPTY_DRAFT}
            isEdit={false}
            users={users}
            onCancel={() => setAdding(false)}
            onSubmit={async (payload) => {
              await createMailAccount(payload);
              setAdding(false);
              load();
            }}
          />
        ) : (
          <button onClick={() => setAdding(true)}>+ Konto hinzufügen</button>
        ))}

      {loading && (
        <p className="muted" role="status">
          Lade …
        </p>
      )}
      {error && (
        <div className="state-block state-block--error">
          <p className="state-block__title">
            Mailkonten konnten nicht geladen werden
          </p>
          <p className="state-block__detail">{error}</p>
          <div className="state-block__action">
            <button onClick={load}>Erneut versuchen</button>
          </div>
        </div>
      )}
      {!loading && !error && accounts.length === 0 && (
        <div className="state-block state-block--subtle">
          <p className="state-block__title">Noch keine Mailkonten</p>
          <p className="state-block__detail">
            {canEdit
              ? "Lege oben ein erstes Postfach an."
              : "Es sind keine Postfächer eingerichtet."}
          </p>
        </div>
      )}

      {accounts.length > 0 && (
        <ul className="field-list">
          {accounts.map((a) => (
            <AccountCard
              key={a.id}
              account={a}
              users={users}
              canEdit={canEdit}
              onChanged={load}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

// Eine Konto-Karte: Anzeige der Verbindungsdaten + Statuszeile (letzter Test),
// „Verbindung testen" mit Ergebnis-Banner, Inline-Bearbeiten und Löschen mit
// Bestätigung (keine window.confirm-Dialoge im dunklen Theme).
function AccountCard({
  account,
  users,
  canEdit,
  onChanged,
}: {
  account: MailAccount;
  users: User[];
  canEdit: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<MailTestResult | null>(null);
  const [testing, setTesting] = useState(false);

  async function remove() {
    setBusy(true);
    setRowError(null);
    try {
      await deleteMailAccount(account.id);
      onChanged();
    } catch (e) {
      setRowError(e instanceof Error ? e.message : String(e));
      setConfirmDelete(false);
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    setTesting(true);
    setRowError(null);
    setTestResult(null);
    try {
      const r = await testMailAccount(account.id);
      setTestResult(r);
      // Kein onChanged(): Der Verbindungstest ist serverseitig zustandslos und
      // aktualisiert weder last_checked_at noch last_error – ein Refetch würde
      // die Statuszeile nicht verändern. Das Banner zeigt das Live-Ergebnis.
    } catch (e) {
      setRowError(e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  }

  if (editing) {
    return (
      <li className="card field-item mail-item">
        <MailAccountForm
          title={`Konto bearbeiten: ${account.name}`}
          submitLabel="Änderungen speichern"
          initial={draftFromAccount(account)}
          isEdit
          hasPassword={account.has_password}
          users={users}
          onCancel={() => setEditing(false)}
          onSubmit={async (payload) => {
            await updateMailAccount(account.id, payload);
            setEditing(false);
            onChanged();
          }}
        />
      </li>
    );
  }

  return (
    <li className="card field-item mail-item">
      <div className="mail-item__head">
        <span className="field-item__name">
          {account.name}
          {!account.enabled && (
            <span className="mail-badge mail-badge--off">deaktiviert</span>
          )}
        </span>
        {canEdit && (
          <div className="field-item__actions">
            <button className="link" onClick={runTest} disabled={testing || busy}>
              {testing ? "Teste …" : "Verbindung testen"}
            </button>
            {confirmDelete ? (
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
                <button
                  className="link"
                  onClick={() => setEditing(true)}
                  disabled={busy}
                >
                  Bearbeiten
                </button>
                <button
                  className="link field-item__delete"
                  onClick={() => setConfirmDelete(true)}
                  aria-label={`Konto ${account.name} löschen`}
                  disabled={busy}
                >
                  Löschen
                </button>
              </>
            )}
          </div>
        )}
      </div>

      <dl className="mail-item__meta muted">
        <div>
          <dt>Server</dt>
          <dd>
            {account.host}:{account.port}{" "}
            {account.use_ssl ? "(SSL/TLS)" : "(unverschlüsselt)"}
          </dd>
        </div>
        <div>
          <dt>Benutzer</dt>
          <dd>{account.username}</dd>
        </div>
        <div>
          <dt>Ordner</dt>
          <dd>{account.folder}</dd>
        </div>
        <div>
          <dt>Eigentümer</dt>
          <dd>{ownerLabel(account.owner, users)}</dd>
        </div>
        {account.password_env && (
          <div>
            <dt>Passwort-Quelle</dt>
            <dd>Secret-Env „{account.password_env}"</dd>
          </div>
        )}
      </dl>

      <StatusLine account={account} />

      {testResult && (
        <p
          className={`status ${testResult.ok ? "status--ok" : "status--error"}`}
          role="status"
        >
          {testResult.message}
        </p>
      )}
      {rowError && <p className="status status--error">{rowError}</p>}
    </li>
  );
}

// Statische Statuszeile (v1: kein Polling): Zeitpunkt des letzten Tests und
// dessen Ergebnis, gespeist aus last_checked_at / last_error.
function StatusLine({ account }: { account: MailAccount }) {
  if (!account.last_checked_at) {
    return <p className="mail-status muted">Noch nicht getestet.</p>;
  }
  const when = new Date(account.last_checked_at).toLocaleString("de-DE");
  if (account.last_error) {
    return (
      <p className="mail-status status--error">
        Letzter Test ({when}): {account.last_error}
      </p>
    );
  }
  return (
    <p className="mail-status status--ok">Zuletzt getestet: {when} — OK</p>
  );
}

// Gemeinsames Formular für Anlegen und Bearbeiten. Beim Bearbeiten zeigt das
// Passwortfeld den Platzhalter „(unverändert lassen)"; leer = keine Änderung.
function MailAccountForm({
  title,
  submitLabel,
  initial,
  isEdit,
  hasPassword = false,
  users,
  onCancel,
  onSubmit,
}: {
  title: string;
  submitLabel: string;
  initial: Draft;
  isEdit: boolean;
  // Nur relevant beim Bearbeiten: steuert, ob das leere Passwortfeld „unverändert
  // lassen" (Passwort hinterlegt) oder „App-Passwort" (noch keins) anzeigt.
  hasPassword?: boolean;
  users: User[];
  onCancel: () => void;
  onSubmit: (payload: MailAccountPayload) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Draft>(initial);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  async function submit() {
    const { error, payload } = validate(draft, isEdit);
    if (error || !payload) {
      setFormError(error);
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      await onSubmit(payload);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card field-form mail-form">
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <div className="field-form__grid">
        <label>
          Name
          <input
            value={draft.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="z. B. Rechnungen"
          />
        </label>
        <label>
          Server (Host)
          <input
            value={draft.host}
            onChange={(e) => set("host", e.target.value)}
            placeholder="imap.example.com"
          />
        </label>
        <label>
          Port
          <input
            type="number"
            min={1}
            max={65535}
            value={draft.port}
            onChange={(e) => set("port", e.target.value)}
          />
        </label>
        <label>
          Benutzername
          <input
            value={draft.username}
            onChange={(e) => set("username", e.target.value)}
            placeholder="post@example.com"
            autoComplete="off"
          />
        </label>
        <label>
          Passwort
          <input
            type="password"
            value={draft.password}
            onChange={(e) => set("password", e.target.value)}
            placeholder={
              isEdit && hasPassword ? "(unverändert lassen)" : "App-Passwort"
            }
            autoComplete="new-password"
          />
        </label>
        <label>
          Ordner
          <input
            value={draft.folder}
            onChange={(e) => set("folder", e.target.value)}
            placeholder="INBOX"
          />
        </label>
        <label>
          Eigentümer
          <select
            value={draft.owner}
            onChange={(e) => set("owner", e.target.value)}
          >
            <option value="">— (Admin-Triage)</option>
            {users.map((u) => (
              <option key={u.id} value={String(u.id)}>
                {u.username}
              </option>
            ))}
            {/* Aktueller Eigentümer nicht (mehr) in der Liste (z. B. deaktiviert):
                als Fallback-Option zeigen, damit er beim Speichern nicht still
                verloren geht. */}
            {draft.owner && !users.some((u) => String(u.id) === draft.owner) && (
              <option value={draft.owner}>Nutzer #{draft.owner}</option>
            )}
          </select>
        </label>
      </div>
      <div className="mail-form__checks">
        <label className="mail-check">
          <input
            type="checkbox"
            checked={draft.use_ssl}
            onChange={(e) => set("use_ssl", e.target.checked)}
          />
          SSL/TLS verwenden (IMAPS, i. d. R. Port 993)
        </label>
        <label className="mail-check">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => set("enabled", e.target.checked)}
          />
          Konto aktiv (Import läuft)
        </label>
      </div>
      {formError && <p className="status status--error">{formError}</p>}
      <div className="mail-form__actions">
        <button onClick={submit} disabled={saving}>
          {saving ? "Speichern …" : submitLabel}
        </button>
        <button className="link" onClick={onCancel} disabled={saving}>
          Abbrechen
        </button>
      </div>
    </section>
  );
}
