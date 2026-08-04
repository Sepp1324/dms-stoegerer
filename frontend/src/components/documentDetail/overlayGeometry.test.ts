import { describe, expect, it } from "vitest";

import { scaleBox, type Bbox } from "./overlayGeometry";

const bbox: Bbox = [10, 20, 30, 50];

describe("scaleBox", () => {
  it("skaliert bbox von Layout- auf Render-Koordinaten (Faktor 2)", () => {
    // Layout 100x100 → gerendert 200x200 ⇒ Faktor 2 pro Achse.
    expect(scaleBox(bbox, 100, 100, 200, 200)).toEqual({
      left: 20,
      top: 40,
      width: 40,
      height: 60,
    });
  });

  it("skaliert Achsen unabhängig", () => {
    // 100x200 → 300x400 ⇒ rx=3, ry=2.
    expect(scaleBox([10, 10, 20, 20], 100, 200, 300, 400)).toEqual({
      left: 30,
      top: 20,
      width: 30,
      height: 20,
    });
  });

  it("liefert null bei fehlenden Maßen (kein Render/Nulldivision)", () => {
    expect(scaleBox(bbox, 0, 100, 200, 200)).toBeNull();
    expect(scaleBox(bbox, 100, 100, 0, 0)).toBeNull();
  });

  it("liefert null bei unvollständiger bbox", () => {
    expect(scaleBox([1, 2, 3] as unknown as Bbox, 100, 100, 200, 200)).toBeNull();
  });
});
