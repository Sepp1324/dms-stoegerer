import { describe, expect, it } from "vitest";

import { DEFAULT_VIEW, pathToView, viewToPath } from "./viewRoutes";

describe("viewRoutes (#7 Stage 1b)", () => {
  it("mappt die Default-View auf / und zurück", () => {
    expect(viewToPath(DEFAULT_VIEW)).toBe("/");
    expect(pathToView("/")).toBe(DEFAULT_VIEW);
    expect(pathToView("")).toBe(DEFAULT_VIEW);
  });

  it("mappt bekannte Views auf ihren Pfad und zurück", () => {
    expect(viewToPath("inbox")).toBe("/inbox");
    expect(pathToView("/inbox")).toBe("inbox");
    expect(pathToView("/quality")).toBe("quality");
    // Zusatzsegmente (z. B. später Query/Unterpfade) stören die View nicht.
    expect(pathToView("/inbox/extra")).toBe("inbox");
  });

  it("fällt bei unbekanntem Pfad auf die Default-View zurück", () => {
    expect(pathToView("/gibtsnicht")).toBe(DEFAULT_VIEW);
    expect(pathToView("/dokument/5")).toBe(DEFAULT_VIEW);
  });
});
