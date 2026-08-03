import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAccessToken, getInboxSummary, login, logout } from "./api";

// P1/P2: Ein bereits laufender Token-Refresh bzw. ein veralteter Request darf einen
// zwischenzeitlichen logout()/login() – auch in einem ANDEREN Browser-Tab – nicht
// rückgängig machen und nicht mit fremder Identität weiterlaufen. Die Sitzungs-ID
// liegt in localStorage (tab-übergreifend geteilt).

const ACCESS_KEY = "dms_access";
const REFRESH_KEY = "dms_refresh";
const SESSION_KEY = "dms_session";

// Realistischer eingeloggter Zustand: Tokens + Sitzungs-ID (wie nach login()).
function seedSession(session = "s1", access = "old-access", refresh = "old-refresh") {
  localStorage.setItem(SESSION_KEY, session);
  localStorage.setItem(ACCESS_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("Refresh/Retry respektiert die (tab-übergreifende) Sitzung", () => {
  it("logout() während eines laufenden Refresh verhindert den Zombie-Token", async () => {
    seedSession();

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/token/refresh/")) {
        // Mitten im laufenden Refresh loggt sich der Nutzer aus.
        logout();
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ access: "zombie-access" }),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 401,
        json: async () => ({}),
      } as unknown as Response);
    });

    await expect(getInboxSummary()).rejects.toThrow();
    expect(getAccessToken()).toBeNull();
  });

  it("ein Refresh mit veraltetem Refresh-Token schreibt seinen Access-Token nicht", async () => {
    seedSession();

    const setSpy = vi.spyOn(Storage.prototype, "setItem");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/token/refresh/")) {
        // Zwischenzeitlich wurde der Refresh-Token rotiert (Sitzung bleibt gleich).
        localStorage.setItem(REFRESH_KEY, "new-refresh");
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ access: "stale-access" }),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 401,
        json: async () => ({}),
      } as unknown as Response);
    });

    await expect(getInboxSummary()).rejects.toThrow();
    expect(setSpy).not.toHaveBeenCalledWith(ACCESS_KEY, "stale-access");
  });

  it("ein veralteter Request loggt eine neue Sitzung NICHT aus", async () => {
    seedSession();

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/token/refresh/")) {
        // Abmeldung + neue Anmeldung während des Refresh des ALTEN Requests.
        logout();
        localStorage.setItem(SESSION_KEY, "s2");
        localStorage.setItem(ACCESS_KEY, "new-access");
        localStorage.setItem(REFRESH_KEY, "new-refresh");
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ access: "stale-access" }),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 401,
        json: async () => ({}),
      } as unknown as Response);
    });

    await expect(getInboxSummary()).rejects.toThrow();
    expect(getAccessToken()).toBe("new-access");
    expect(localStorage.getItem(REFRESH_KEY)).toBe("new-refresh");
  });

  it("ein veralteter Request wird nach Neuanmeldung NICHT mit der neuen Identität wiederholt", async () => {
    seedSession();

    let apiCalls = 0;
    let apiRetried = false;
    let refreshAttempted = false;

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/token/") && !url.includes("refresh")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ access: "new-access", refresh: "new-refresh" }),
        } as unknown as Response);
      }
      if (url.includes("/auth/token/refresh/")) {
        refreshAttempted = true;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ access: "stale-access" }),
        } as unknown as Response);
      }
      apiCalls += 1;
      if (apiCalls === 1) {
        // Während des ersten Versuchs meldet sich ein neuer Nutzer an (echtes
        // login() setzt eine neue Sitzungs-ID).
        return login("neu", "pw").then(
          () =>
            ({ ok: false, status: 401, json: async () => ({}) }) as unknown as Response,
        );
      }
      apiRetried = true;
      return Promise.resolve({
        ok: false,
        status: 401,
        json: async () => ({}),
      } as unknown as Response);
    });

    await expect(getInboxSummary()).rejects.toThrow();
    expect(refreshAttempted).toBe(false);
    expect(apiRetried).toBe(false);
    expect(apiCalls).toBe(1);
    expect(getAccessToken()).toBe("new-access");
    expect(localStorage.getItem(REFRESH_KEY)).toBe("new-refresh");
  });

  it("ein Login in einem ANDEREN Tab entwertet Requests in diesem Tab (P1)", async () => {
    // Tab A ist als Nutzer A eingeloggt.
    seedSession("tabA", "a-access", "a-refresh");

    let apiCalls = 0;
    let apiRetried = false;
    let refreshAttempted = false;

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/token/refresh/")) {
        refreshAttempted = true;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ access: "a-refreshed" }),
        } as unknown as Response);
      }
      apiCalls += 1;
      if (apiCalls === 1) {
        // Tab B meldet sich als anderer Nutzer an: localStorage (Sitzung + Tokens)
        // ist tab-übergreifend geteilt und ändert sich damit auch für Tab A.
        localStorage.setItem(SESSION_KEY, "tabB");
        localStorage.setItem(ACCESS_KEY, "b-access");
        localStorage.setItem(REFRESH_KEY, "b-refresh");
        return Promise.resolve({
          ok: false,
          status: 401,
          json: async () => ({}),
        } as unknown as Response);
      }
      apiRetried = true;
      return Promise.resolve({
        ok: false,
        status: 401,
        json: async () => ({}),
      } as unknown as Response);
    });

    await expect(getInboxSummary()).rejects.toThrow();
    // Weder Refresh noch Retry unter Tab Bs Identität ...
    expect(refreshAttempted).toBe(false);
    expect(apiRetried).toBe(false);
    expect(apiCalls).toBe(1);
    // ... und Tab Bs Sitzung bleibt unangetastet (Tab A loggt sie nicht aus).
    expect(localStorage.getItem(ACCESS_KEY)).toBe("b-access");
    expect(localStorage.getItem(REFRESH_KEY)).toBe("b-refresh");
    expect(localStorage.getItem(SESSION_KEY)).toBe("tabB");
  });
});
