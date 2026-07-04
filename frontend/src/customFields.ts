// Formatierung & Konvertierung für Zusatzfelder (Custom Fields, STOAA-113).
//
// Kanonisches Storage-Format (Backend-Kontrakt STOAA-112):
//   text     → roh
//   number   → Punkt-Dezimal, z. B. "1234.56"
//   currency → wie number, z. B. "1234.56"
//   date     → ISO "YYYY-MM-DD"
//   boolean  → "true" / "false" (leer = nicht gesetzt)
//
// Das Frontend zeigt Werte im deutschen Format an und konvertiert Eingaben beim
// Speichern zurück ins kanonische Format.
import type { CustomFieldDataType } from "./api";

// Deutsche Label für Datentypen (Admin-Anzeige + Dropdown).
export const DATA_TYPE_LABELS: Record<CustomFieldDataType, string> = {
  text: "Text",
  number: "Zahl",
  date: "Datum",
  currency: "Währung",
  boolean: "Ja/Nein",
};

// Reihenfolge fürs Anlege-Dropdown.
export const DATA_TYPE_OPTIONS: CustomFieldDataType[] = [
  "text",
  "number",
  "date",
  "currency",
  "boolean",
];

// Kanonischen Wert für die Anzeige aufbereiten. Leerer Wert → Em-Dash.
export function formatCustomFieldValue(
  value: string,
  dataType: CustomFieldDataType,
): string {
  if (value == null || value === "") return "—";
  switch (dataType) {
    case "number":
      return formatDecimalDe(value, false);
    case "currency":
      return formatDecimalDe(value, true);
    case "date":
      return formatIsoDateDe(value);
    case "boolean":
      return value === "true" ? "Ja" : value === "false" ? "Nein" : "—";
    case "text":
    default:
      return value;
  }
}

// Kanonischen Wert in den Wert für das jeweilige Edit-Input umwandeln.
// - number/currency: Punkt-Dezimal → deutsches Komma (ohne Tausenderpunkte)
// - date/boolean/text: unverändert (Input-Formate entsprechen dem Storage)
export function toInputValue(
  value: string,
  dataType: CustomFieldDataType,
): string {
  if (value == null) return "";
  if ((dataType === "number" || dataType === "currency") && value !== "") {
    return value.replace(".", ",");
  }
  return value;
}

// Ergebnis der Eingabe-Konvertierung: entweder ein kanonischer Wert oder ein
// Validierungsfehler (deutsche Meldung fürs Inline-Error).
export interface ParseResult {
  value?: string;
  error?: string;
}

// Rohe Eingabe eines Edit-Inputs in den kanonischen Storage-Wert umwandeln und
// dabei validieren. Leere Eingabe ist immer gültig (= Wert entfernen).
export function toCanonicalValue(
  raw: string,
  dataType: CustomFieldDataType,
): ParseResult {
  const trimmed = (raw ?? "").trim();
  if (trimmed === "") return { value: "" };

  switch (dataType) {
    case "number":
    case "currency": {
      const canonical = germanDecimalToCanonical(trimmed);
      if (canonical === null) {
        return { error: "Ungültige Zahl – z. B. 1234,56" };
      }
      return { value: canonical };
    }
    case "date": {
      // <input type="date"> liefert bereits ISO; defensiv trotzdem prüfen.
      if (!/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
        return { error: "Ungültiges Datum" };
      }
      return { value: trimmed };
    }
    case "boolean": {
      if (trimmed !== "true" && trimmed !== "false") {
        return { error: "Ungültiger Wert" };
      }
      return { value: trimmed };
    }
    case "text":
    default:
      return { value: trimmed };
  }
}

// --- interne Helfer ---

// Deutsche Zahleneingabe ("1.234,56" oder "1234,56" oder "1234.56") in
// kanonisches Punkt-Dezimal umwandeln. Gibt null bei ungültiger Eingabe zurück.
function germanDecimalToCanonical(input: string): string | null {
  const cleaned = input.replace(/\s/g, "");
  let normalized: string;
  if (cleaned.includes(",")) {
    // Komma = Dezimaltrenner, Punkte sind Tausendertrennzeichen.
    normalized = cleaned.replace(/\./g, "").replace(",", ".");
  } else {
    // Kein Komma: ein einzelner Punkt gilt als Dezimaltrenner (Toleranz).
    normalized = cleaned;
  }
  if (!/^-?\d+(\.\d+)?$/.test(normalized)) return null;
  return normalized;
}

// Punkt-Dezimal-Wert deutsch formatieren (Tausenderpunkt, Dezimalkomma),
// optional mit €-Suffix. Fällt bei nicht-numerischen Werten auf den Rohwert
// zurück (sollte dank Backend-Kontrakt nicht vorkommen).
function formatDecimalDe(canonical: string, withCurrency: boolean): string {
  const n = Number(canonical);
  if (!Number.isFinite(n)) return canonical;
  const decimals = canonical.includes(".")
    ? canonical.split(".")[1].length
    : 0;
  const formatted = n.toLocaleString("de-DE", {
    minimumFractionDigits: withCurrency ? Math.max(2, decimals) : decimals,
    maximumFractionDigits: withCurrency ? Math.max(2, decimals) : 20,
  });
  return withCurrency ? `${formatted} €` : formatted;
}

// ISO-Datum (YYYY-MM-DD) → DD.MM.YYYY; ungültige Werte unverändert lassen.
function formatIsoDateDe(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${m[3]}.${m[2]}.${m[1]}`;
}
