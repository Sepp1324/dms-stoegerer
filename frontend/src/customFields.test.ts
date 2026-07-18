import { describe, expect, it } from "vitest";

import {
  formatCustomFieldValue,
  toCanonicalValue,
  toInputValue,
} from "./customFields";

// Zusatzfeld-Konvertierung (STOAA-113): deutsches Eingabe-/Anzeigeformat <->
// kanonisches Storage-Format (Punkt-Dezimal, ISO-Datum, true/false).

describe("toCanonicalValue (Eingabe → Storage)", () => {
  it("number: dt. Format mit Tausenderpunkt + Dezimalkomma", () => {
    expect(toCanonicalValue("1.234,56", "number")).toEqual({ value: "1234.56" });
  });

  it("currency: einfaches Dezimalkomma", () => {
    expect(toCanonicalValue("1234,56", "currency")).toEqual({ value: "1234.56" });
  });

  it("toleriert Punkt-Dezimal ohne Komma", () => {
    expect(toCanonicalValue("1234.56", "number")).toEqual({ value: "1234.56" });
  });

  it("weist ungültige Zahlen mit Fehler ab", () => {
    expect(toCanonicalValue("abc", "number").error).toBeTruthy();
    expect(toCanonicalValue("abc", "number").value).toBeUndefined();
  });

  it("leere Eingabe ist gültig (= Wert entfernen)", () => {
    expect(toCanonicalValue("   ", "currency")).toEqual({ value: "" });
  });

  it("date: ISO gültig, dt. Format ungültig", () => {
    expect(toCanonicalValue("2026-05-28", "date")).toEqual({ value: "2026-05-28" });
    expect(toCanonicalValue("28.05.2026", "date").error).toBeTruthy();
  });

  it("boolean: nur true/false", () => {
    expect(toCanonicalValue("true", "boolean")).toEqual({ value: "true" });
    expect(toCanonicalValue("ja", "boolean").error).toBeTruthy();
  });

  it("text: nur getrimmt", () => {
    expect(toCanonicalValue("  Hallo  ", "text")).toEqual({ value: "Hallo" });
  });
});

describe("formatCustomFieldValue (Storage → Anzeige)", () => {
  it("number deutsch mit Tausenderpunkt", () => {
    expect(formatCustomFieldValue("1234.56", "number")).toBe("1.234,56");
  });

  it("currency mit €-Suffix und min. 2 Nachkommastellen", () => {
    expect(formatCustomFieldValue("1234.5", "currency")).toBe("1.234,50 €");
  });

  it("date DD.MM.YYYY", () => {
    expect(formatCustomFieldValue("2026-05-28", "date")).toBe("28.05.2026");
  });

  it("boolean Ja/Nein", () => {
    expect(formatCustomFieldValue("true", "boolean")).toBe("Ja");
    expect(formatCustomFieldValue("false", "boolean")).toBe("Nein");
  });

  it("leerer Wert → Em-Dash", () => {
    expect(formatCustomFieldValue("", "text")).toBe("—");
  });
});

describe("toInputValue (Storage → Edit-Input)", () => {
  it("number: Punkt-Dezimal → Komma", () => {
    expect(toInputValue("1234.56", "number")).toBe("1234,56");
  });

  it("date/text unverändert", () => {
    expect(toInputValue("2026-05-28", "date")).toBe("2026-05-28");
    expect(toInputValue("Hallo", "text")).toBe("Hallo");
  });
});

describe("Round-Trip Eingabe → Storage → Input", () => {
  it("currency bleibt konsistent", () => {
    const canonical = toCanonicalValue("1234,56", "currency").value ?? "";
    expect(canonical).toBe("1234.56");
    expect(toInputValue(canonical, "currency")).toBe("1234,56");
  });
});
