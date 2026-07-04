import { useState } from "react";
import { login } from "../api";

export default function Login({
  onSuccess,
  hint,
}: {
  onSuccess: () => void;
  // Optionaler Kontext-Hinweis, z. B. wenn die Anmeldung für einen Freigabelink
  // erzwungen wird (STOAA-193).
  hint?: string;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username, password);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card login-card" onSubmit={submit}>
        <h1>DMS</h1>
        <p className="subtitle">{hint ?? "Anmeldung"}</p>

        <label>
          Benutzername
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            autoComplete="username"
          />
        </label>
        <label>
          Passwort
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>

        {error && <p className="status status--error">{error}</p>}

        <button type="submit" disabled={busy || !username || !password}>
          {busy ? "Anmelden …" : "Anmelden"}
        </button>
      </form>
    </div>
  );
}
