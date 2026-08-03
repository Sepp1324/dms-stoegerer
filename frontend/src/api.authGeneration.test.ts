import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getAccessToken,
  getInboxSummary,
  login,
  logout,
  onAuthChange,
} from "./api";

// P1/P2: Refresh/Retry und Logout respektieren die tab-übergreifende Sitzung, der
// Auth-State liegt ATOMAR in einem JSON-Key, und logout() invalidiert den
// Refresh-Token best-effort serverseitig.

const AUTH_KEY = "dms_auth_state";

function seed(session = "s1", access = "old-access", refresh = "old-refresh") {
  localStorage.setItem(AUTH_KEY, JSON.stringify({ session, access, refresh }));
}
function readAuth(): { session: string; access: string; refresh: string } | null {
  const raw = localStorage.getItem(AUTH_KEY);
  return raw ? JSON.parse(raw) : null;
}
function ok(json: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => json } as unknown as Response;
}
function unauth(): Response {
  return { ok: false, status: 401, json: async () => ({}) } as unknown as Response;
}

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("Auth-State: atomar, tab-übergreifend, serverseitig invalidiert", () => {
  it("logout() während eines laufenden Refresh verhindert den Zombie-Token", async () => {
    seed();
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/logout/")) return Promise.resolve(ok({}, 205));
      if (url.includes("/auth/token/refresh/")) {
        logout();
        return Promise.resolve(ok({ access: "zombie-access" }));
      }
      return Promise.resolve(unauth());
    });

    await expect(getInboxSummary()).rejects.toThrow();
    expect(getAccessToken()).toBeNull();
  });

  it("ein Refresh mit veraltetem Refresh-Token schreibt seinen Access-Token nicht", async () => {
    seed();
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/token/refresh/")) {
        // Refresh-Token wurde zwischenzeitlich rotiert (Sitzung bleibt).
        localStorage.setItem(
          AUTH_KEY,
          JSON.stringify({ session: "s1", access: "old-access", refresh: "new-refresh" }),
        );
        return Promise.resolve(ok({ access: "stale-access" }));
      }
      return Promise.resolve(unauth());
    });

    await expect(getInboxSummary()).rejects.toThrow();
    expect(readAuth()?.access).not.toBe("stale-access");
  });

  it("ein veralteter Request loggt eine neue Sitzung NICHT aus", async () => {
    seed();
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/logout/")) return Promise.resolve(ok({}, 205));
      if (url.includes("/auth/token/refresh/")) {
        logout();
        localStorage.setItem(
          AUTH_KEY,
          JSON.stringify({ session: "s2", access: "new-access", refresh: "new-refresh" }),
        );
        return Promise.resolve(ok({ access: "stale-access" }));
      }
      return Promise.resolve(unauth());
    });

    await expect(getInboxSummary()).rejects.toThrow();
    expect(getAccessToken()).toBe("new-access");
    expect(readAuth()?.refresh).toBe("new-refresh");
  });

  it("ein veralteter Request wird nach Neuanmeldung NICHT mit der neuen Identität wiederholt", async () => {
    seed();
    let apiCalls = 0;
    let apiRetried = false;
    let refreshAttempted = false;

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/token/") && !url.includes("refresh")) {
        return Promise.resolve(ok({ access: "new-access", refresh: "new-refresh" }));
      }
      if (url.includes("/auth/token/refresh/")) {
        refreshAttempted = true;
        return Promise.resolve(ok({ access: "stale-access" }));
      }
      apiCalls += 1;
      if (apiCalls === 1) {
        return login("neu", "pw").then(() => unauth());
      }
      apiRetried = true;
      return Promise.resolve(unauth());
    });

    await expect(getInboxSummary()).rejects.toThrow();
    expect(refreshAttempted).toBe(false);
    expect(apiRetried).toBe(false);
    expect(apiCalls).toBe(1);
    expect(getAccessToken()).toBe("new-access");
    expect(readAuth()?.refresh).toBe("new-refresh");
  });

  it("ein Login in einem ANDEREN Tab entwertet Requests in diesem Tab (P1)", async () => {
    seed("tabA", "a-access", "a-refresh");
    let apiCalls = 0;
    let apiRetried = false;
    let refreshAttempted = false;

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/token/refresh/")) {
        refreshAttempted = true;
        return Promise.resolve(ok({ access: "a-refreshed" }));
      }
      apiCalls += 1;
      if (apiCalls === 1) {
        // Tab B meldet sich als anderer Nutzer an (localStorage ist tab-geteilt).
        localStorage.setItem(
          AUTH_KEY,
          JSON.stringify({ session: "tabB", access: "b-access", refresh: "b-refresh" }),
        );
        return Promise.resolve(unauth());
      }
      apiRetried = true;
      return Promise.resolve(unauth());
    });

    await expect(getInboxSummary()).rejects.toThrow();
    expect(refreshAttempted).toBe(false);
    expect(apiRetried).toBe(false);
    expect(apiCalls).toBe(1);
    // Tab Bs Sitzung bleibt intakt.
    expect(readAuth()).toEqual({
      session: "tabB",
      access: "b-access",
      refresh: "b-refresh",
    });
  });

  it("logout() ruft /api/auth/logout/ best-effort mit dem Refresh-Token auf (P1)", () => {
    seed("s1", "a", "the-refresh");
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({}, 205));

    logout();

    const call = fetchSpy.mock.calls.find((c) =>
      String(c[0]).includes("/auth/logout/"),
    );
    expect(call).toBeTruthy();
    expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
      refresh: "the-refresh",
    });
    // Lokal ist der State atomar entfernt.
    expect(localStorage.getItem(AUTH_KEY)).toBeNull();
  });

  it("onAuthChange benachrichtigt bei logout()", () => {
    seed();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(ok({}, 205));
    let called = 0;
    const unsub = onAuthChange(() => {
      called += 1;
    });
    logout();
    expect(called).toBe(1);
    unsub();
  });

  it("der Auth-State liegt in EINEM Key (atomar) statt in drei", () => {
    seed("s1", "acc", "ref");
    expect(localStorage.getItem("dms_auth_state")).toBeTruthy();
    // Keine losen Alt-Keys mehr.
    expect(localStorage.getItem("dms_access")).toBeNull();
    expect(localStorage.getItem("dms_refresh")).toBeNull();
    expect(localStorage.getItem("dms_session")).toBeNull();
    expect(getAccessToken()).toBe("acc");
  });
});
