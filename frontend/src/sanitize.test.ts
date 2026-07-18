import { describe, expect, it } from "vitest";

import { sanitizeSnippet } from "./sanitize";

describe("sanitizeSnippet", () => {
  it("behält <mark>-Hervorhebungen", () => {
    const out = sanitizeSnippet("Rechnung <mark>Energie</mark> 2026");
    expect(out).toContain("<mark>Energie</mark>");
  });

  it("entfernt Skripte und Event-Handler (XSS-Schutz)", () => {
    const out = sanitizeSnippet(
      '<img src=x onerror="alert(1)"><script>alert(1)</script>hallo',
    );
    expect(out.toLowerCase()).not.toContain("<script");
    expect(out.toLowerCase()).not.toContain("onerror");
    expect(out).toContain("hallo");
  });

  it("liefert leeren String für null/undefined", () => {
    expect(sanitizeSnippet(null)).toBe("");
    expect(sanitizeSnippet(undefined)).toBe("");
  });
});
