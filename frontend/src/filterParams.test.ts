import { describe, expect, it } from "vitest";

import { buildFilterParams, type ListFilters } from "./filterParams";

const EMPTY: ListFilters = {
  q: "",
  correspondent: "",
  documentType: "",
  tag: "",
  storagePath: "",
  folder: "",
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
      page: 2,
    });
    expect(p.get("q")).toBe("rechnung");
    expect(p.get("folder")).toBe("5");
    expect(p.get("document_type")).toBe("3");
    expect(p.get("page")).toBe("2");
  });

  it("behält den Sonderwert folder=none und lässt page=1 weg", () => {
    const p = buildFilterParams({ ...EMPTY, folder: "none", page: 1 });
    expect(p.get("folder")).toBe("none");
    expect(p.has("page")).toBe(false);
  });
});
