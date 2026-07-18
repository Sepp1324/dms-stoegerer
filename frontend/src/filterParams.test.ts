import { describe, expect, it } from "vitest";

import {
  buildFilterParams,
  parseProcessingState,
  parseSharedScope,
  type ListFilters,
} from "./filterParams";

const EMPTY: ListFilters = {
  q: "",
  correspondent: "",
  documentType: "",
  tag: "",
  storagePath: "",
  folder: "",
  processingState: "",
  sharedScope: "",
  ordering: "",
  page: 1,
};

describe("buildFilterParams (#7 Stage 2)", () => {
  it("lässt leere Filter aus (knappe URL)", () => {
    expect(buildFilterParams(EMPTY).toString()).toBe("");
  });

  it("serialisiert gesetzte Filter mit Backend-Param-Namen", () => {
    const p = buildFilterParams({
      ...EMPTY,
      q: "rechnung",
      folder: 5,
      documentType: 3,
      processingState: "failed",
      sharedScope: "with-me",
      page: 2,
    });
    expect(p.get("q")).toBe("rechnung");
    expect(p.get("folder")).toBe("5");
    expect(p.get("document_type")).toBe("3");
    expect(p.get("processing_state")).toBe("failed");
    expect(p.get("shared")).toBe("with-me");
    expect(p.get("page")).toBe("2");
  });

  it("behält den Sonderwert folder=none und lässt page=1 weg", () => {
    const p = buildFilterParams({ ...EMPTY, folder: "none", page: 1 });
    expect(p.get("folder")).toBe("none");
    expect(p.has("page")).toBe(false);
  });
});

describe("Parse-Guards (#7 Stage 2b)", () => {
  it("parseProcessingState akzeptiert nur zulässige Werte", () => {
    expect(parseProcessingState("failed")).toBe("failed");
    expect(parseProcessingState("bogus")).toBe("");
    expect(parseProcessingState(null)).toBe("");
  });

  it("parseSharedScope akzeptiert nur with-me/by-me", () => {
    expect(parseSharedScope("by-me")).toBe("by-me");
    expect(parseSharedScope("everyone")).toBe("");
    expect(parseSharedScope(null)).toBe("");
  });
});
