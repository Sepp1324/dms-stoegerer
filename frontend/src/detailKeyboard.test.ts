import { describe, expect, it } from "vitest";

import { shouldCloseOnEscape } from "./detailKeyboard";

describe("shouldCloseOnEscape", () => {
  it("schließt bei Escape ohne Fokus/Edit", () => {
    expect(shouldCloseOnEscape("Escape", false)).toBe(true);
    expect(shouldCloseOnEscape("Escape", false, "DIV")).toBe(true);
    expect(shouldCloseOnEscape("Escape", false, "BUTTON")).toBe(true);
  });

  it("ignoriert andere Tasten", () => {
    expect(shouldCloseOnEscape("Enter", false)).toBe(false);
    expect(shouldCloseOnEscape("k", false)).toBe(false);
  });

  it("schließt nicht im Edit-Modus", () => {
    expect(shouldCloseOnEscape("Escape", true)).toBe(false);
  });

  it("schließt nicht, während man in einem Eingabefeld tippt", () => {
    expect(shouldCloseOnEscape("Escape", false, "INPUT")).toBe(false);
    expect(shouldCloseOnEscape("Escape", false, "TEXTAREA")).toBe(false);
    expect(shouldCloseOnEscape("Escape", false, "SELECT")).toBe(false);
    expect(shouldCloseOnEscape("Escape", false, "input")).toBe(false);
  });

  it("schließt nicht in contentEditable", () => {
    expect(shouldCloseOnEscape("Escape", false, "DIV", true)).toBe(false);
  });
});
