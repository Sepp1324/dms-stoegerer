import { describe, expect, it } from "vitest";

import { documentLink } from "./documentLink";

describe("documentLink", () => {
  it("hängt /dokument/:id an den aktuellen Origin", () => {
    const link = documentLink(42);
    expect(link.startsWith(window.location.origin)).toBe(true);
    expect(link).toMatch(/\/dokument\/42$/);
  });
});
