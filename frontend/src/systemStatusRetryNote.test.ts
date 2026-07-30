import { describe, expect, it } from "vitest";

import { formatRetryNote } from "./components/SystemStatusPage";
import type { OCRRetryResult } from "./api";

// P2: Der Bulk-Retry darf einen Teilerfolg (202 mit failed_ids) nicht als reinen
// Erfolg darstellen – sonst bleiben nicht erneut eingeplante Dokumente unbemerkt.
function result(partial: Partial<OCRRetryResult>): OCRRetryResult {
  return { queued: 0, limit: 25, version_ids: [], ...partial };
}

describe("formatRetryNote", () => {
  it("meldet nur den Erfolg, wenn nichts fehlschlug", () => {
    const note = formatRetryNote(result({ queued: 3, version_ids: [1, 2, 3] }));
    expect(note).toBe("3 Verarbeitungen neu angestoßen.");
    expect(note).not.toMatch(/nicht eingeplant/);
  });

  it("macht Teilfehler sichtbar (failed_ids)", () => {
    const note = formatRetryNote(
      result({ queued: 2, version_ids: [1, 2], failed_ids: [3, 4, 5] }),
    );
    expect(note).toMatch(/2 Verarbeitungen neu angestoßen\./);
    expect(note).toMatch(/3 konnten nicht eingeplant werden/);
  });

  it("Singular bei genau einem Fehlschlag", () => {
    const note = formatRetryNote(result({ queued: 1, failed_ids: [9] }));
    expect(note).toMatch(/1 konnte nicht eingeplant werden/);
  });

  it("leere failed_ids zählen nicht als Fehler", () => {
    const note = formatRetryNote(result({ queued: 1, failed_ids: [] }));
    expect(note).toBe("1 Verarbeitung neu angestoßen.");
  });
});
