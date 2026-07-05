import { useState } from "react";
import type { NamedRef } from "../../api";
import { InlineCreate } from "./InlineCreate";

// Auswahl mit „+ neu"-Inline-Anlage (STOAA-431, aus DocumentDetail.tsx
// extrahiert). Verhalten unverändert.
export function CreatableSelect({
  label,
  value,
  onChange,
  options,
  onCreate,
}: {
  label: string;
  value: number | "";
  onChange: (v: number | "") => void;
  options: NamedRef[];
  onCreate: (name: string) => Promise<NamedRef>;
}) {
  const [adding, setAdding] = useState(false);
  return (
    <label>
      {label}
      <div className="creatable">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value ? Number(e.target.value) : "")}
        >
          <option value="">— keiner —</option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
        <button type="button" className="link" onClick={() => setAdding((a) => !a)}>
          + neu
        </button>
      </div>
      {adding && (
        <InlineCreate
          placeholder="Name"
          buttonLabel="Anlegen"
          onCreate={async (name) => {
            const item = await onCreate(name);
            onChange(item.id);
            setAdding(false);
          }}
        />
      )}
    </label>
  );
}
