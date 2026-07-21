import { afterEach, describe, expect, it, vi } from "vitest";

import { getDocumentPreview } from "./api";

// P0-Defense-in-depth: getDocumentPreview darf nur PDF/Raster-Bilder als Blob
// zurückgeben. Ein als text/html (oder SVG) gelieferter Polyglot dürfte NIE in
// das un-sandboxed Vorschau-iframe – sonst Script-Ausführung im DMS-Origin.
afterEach(() => vi.restoreAllMocks());

function mockBlob(type: string) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    status: 200,
    blob: async () => new Blob(["x"], { type }),
  } as unknown as Response);
}

describe("getDocumentPreview – Blob-Typ-Prüfung", () => {
  it("gibt einen PDF-Blob zurück", async () => {
    mockBlob("application/pdf");
    const blob = await getDocumentPreview(1);
    expect(blob.type).toBe("application/pdf");
  });

  it("akzeptiert Raster-Bilder", async () => {
    mockBlob("image/png");
    const blob = await getDocumentPreview(1);
    expect(blob.type).toBe("image/png");
  });

  it("wirft bei text/html (Polyglot)", async () => {
    mockBlob("text/html");
    await expect(getDocumentPreview(1)).rejects.toThrow(/nicht unterstützter/i);
  });

  it("wirft bei SVG (aktives XML, trotz image/*)", async () => {
    mockBlob("image/svg+xml");
    await expect(getDocumentPreview(1)).rejects.toThrow(/nicht unterstützter/i);
  });

  it("wirft bei HTTP-Fehlerstatus (z. B. 415)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 415,
      blob: async () => new Blob([]),
    } as unknown as Response);
    await expect(getDocumentPreview(1)).rejects.toThrow(/HTTP 415/);
  });
});
