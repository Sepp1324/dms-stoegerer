import { useState } from "react";

// Kleines Inline-Formular „Name eingeben + anlegen" (STOAA-431, aus
// DocumentDetail.tsx extrahiert). Wird von CreatableSelect und dem Edit-Formular
// (Schlagwort anlegen) verwendet. Verhalten unverändert.
export function InlineCreate({
  placeholder,
  buttonLabel,
  onCreate,
}: {
  placeholder: string;
  buttonLabel: string;
  onCreate: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await onCreate(name.trim());
      setName("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="creatable-new">
      <div style={{ display: "flex", gap: "0.4rem" }}>
        <input
          value={name}
          placeholder={placeholder}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              create();
            }
          }}
        />
        <button type="button" onClick={create} disabled={busy || !name.trim()}>
          {busy ? "…" : buttonLabel}
        </button>
      </div>
      {err && <span className="status status--error">{err}</span>}
    </div>
  );
}
