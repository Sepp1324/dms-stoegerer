import { afterEach, describe, expect, it, vi } from "vitest";

import { getDocument, getDocumentIntegrity } from "./api";

// #8: Die Detail-Fetches reichen ein AbortSignal bis fetch durch, damit die
// Effekte bei schnellem Dokumentwechsel wirklich abbrechen können.
afterEach(() => vi.restoreAllMocks());

function mockOk() {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({}),
  } as unknown as Response);
}

describe("Detail-Fetches reichen das AbortSignal durch", () => {
  it("getDocument", async () => {
    const ctrl = new AbortController();
    const fetchSpy = mockOk();
    await getDocument(5, ctrl.signal);
    const opts = fetchSpy.mock.calls[0][1] as RequestInit;
    expect(opts.signal).toBe(ctrl.signal);
  });

  it("getDocumentIntegrity", async () => {
    const ctrl = new AbortController();
    const fetchSpy = mockOk();
    await getDocumentIntegrity(9, ctrl.signal);
    const opts = fetchSpy.mock.calls[0][1] as RequestInit;
    expect(opts.signal).toBe(ctrl.signal);
  });
});
