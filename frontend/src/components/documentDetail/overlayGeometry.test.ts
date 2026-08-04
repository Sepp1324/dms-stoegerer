import { describe, expect, it } from "vitest";

import {
  countMatches,
  normalizeText,
  positionWords,
  type LayoutWord,
} from "./overlayGeometry";

const words: LayoutWord[] = [
  { t: "Rechnung", bbox: [10, 20, 30, 40] },
  { t: "Müller", bbox: [50, 60, 70, 80] },
];

describe("normalizeText", () => {
  it("ist case-insensitiv und entfernt Diakritika", () => {
    expect(normalizeText("MÜLLER")).toBe("muller");
    expect(normalizeText("Rechnung")).toBe("rechnung");
  });
});

describe("positionWords", () => {
  it("skaliert bbox von Layout- auf Render-Koordinaten (Faktor 2)", () => {
    // Layout 100x100 → gerendert 200x200 ⇒ Faktor 2 pro Achse.
    const pos = positionWords(words, 100, 100, 200, 200, "");
    expect(pos[0]).toMatchObject({
      left: 20,
      top: 40,
      width: 40,
      height: 40,
      match: false,
    });
  });

  it("markiert Treffer diakritika- und case-tolerant", () => {
    const pos = positionWords(words, 100, 100, 200, 200, "muller");
    expect(pos[0].match).toBe(false);
    expect(pos[1].match).toBe(true);
    expect(countMatches(pos)).toBe(1);
  });

  it("liefert leer bei fehlenden Maßen (kein Render/Nulldivision)", () => {
    expect(positionWords(words, 0, 100, 200, 200, "x")).toEqual([]);
    expect(positionWords(words, 100, 100, 0, 0, "x")).toEqual([]);
  });

  it("markiert nichts bei leerer Suche", () => {
    expect(countMatches(positionWords(words, 100, 100, 200, 200, "  "))).toBe(0);
  });
});
