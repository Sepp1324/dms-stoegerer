import { afterEach, describe, expect, it, vi } from "vitest";

import { getInboxCandidates } from "./api";

// Guard für den Inbox-Batch (#1/#232): kein Netz bei leerer id-Liste, und die
// String-Keys der JSON-Antwort werden in numerische Keys übersetzt.
afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(body: unknown, ok = true, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok,
    status,
    json: async () => body,
  } as unknown as Response);
}

describe("getInboxCandidates", () => {
  it("ruft NICHT das Netz und liefert {} bei leerer id-Liste", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(getInboxCandidates([])).resolves.toEqual({});
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("übersetzt String-Keys der Antwort in numerische Keys", async () => {
    mockFetch({ "5": { extraction: [], cases: [{ id: 1 }] } });
    const res = await getInboxCandidates([5, 7]);
    expect(res[5]).toEqual({ extraction: [], cases: [{ id: 1 }] });
    expect(res[7]).toBeUndefined();
  });

  it("übergibt die ids als kommaseparierten Query-Param", async () => {
    const fetchSpy = mockFetch({});
    await getInboxCandidates([3, 8, 12]);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const url = String((fetchSpy.mock.calls[0] as unknown[])[0]);
    expect(url).toContain("/documents/inbox-candidates/?ids=3,8,12");
  });

  it("wirft bei HTTP-Fehler", async () => {
    mockFetch({}, false, 500);
    await expect(getInboxCandidates([1])).rejects.toThrow(/HTTP 500/);
  });
});
