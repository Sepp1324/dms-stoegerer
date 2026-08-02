import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAccessToken, getInboxSummary, login, logout } from "./api";

// P2: Ein bereits laufender Token-Refresh darf einen zwischenzeitlichen
// logout()/login() NICHT rückgängig machen. Ohne die Auth-Generation legte ein
// nach dem Logout eintreffender Refresh wieder einen gültigen Access-Token in den
// Storage (Zombie-Session) bzw. überschrieb die Tokens einer folgenden Anmeldung.

const ACCESS_KEY = "dms_access";
const REFRESH_KEY = "dms_refresh";

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("Refresh respektiert die Auth-Generation", () => {
  it("logout() während eines laufenden Refresh verhindert den Zombie-Token", async () => {
    localStorage.setItem(ACCESS_KEY, "old-access");
    localStorage.setItem(REFRESH_KEY, "old-refresh");

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
      // Eigentlicher API-Request (und Retry) -> 401, erzwingt den Refresh.
      return Promise.resolve({
        ok: false,
        status: 401,
        json: async () => ({}),
      } as unknown as Response);
    });

    await expect(getInboxSummary()).rejects.toThrow();
    // Der Refresh-Erfolg darf NICHT angewendet worden sein.
    expect(getAccessToken()).toBeNull();
  });

  it("ein Refresh mit veraltetem Refresh-Token schreibt seinen Access-Token nicht", async () => {
    localStorage.setItem(ACCESS_KEY, "old-access");
    localStorage.setItem(REFRESH_KEY, "old-refresh");

    const setSpy = vi.spyOn(Storage.prototype, "setItem");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/token/refresh/")) {
        // Zwischenzeitlich hat ein anderer Flow (z. B. eine neue Anmeldung) den
        // Refresh-Token rotiert -> der hier verwendete ist nicht mehr aktuell.
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
    // Der veraltete Refresh darf seinen Access-Token nicht angewendet haben.
    expect(setSpy).not.toHaveBeenCalledWith(ACCESS_KEY, "stale-access");
  });

  it("ein veralteter Request loggt eine neue Sitzung NICHT aus", async () => {
    localStorage.setItem(ACCESS_KEY, "old-access");
    localStorage.setItem(REFRESH_KEY, "old-refresh");

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/token/refresh/")) {
        // Während des Refresh des ALTEN Requests: Abmeldung + neue Anmeldung.
        // (logout() erhöht die Generation; die neuen Tokens stehen danach.)
        logout();
        localStorage.setItem(ACCESS_KEY, "new-access");
        localStorage.setItem(REFRESH_KEY, "new-refresh");
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ access: "stale-access" }),
        } as unknown as Response);
      }
      // Der alte Request bleibt 401 (Retry ebenfalls).
      return Promise.resolve({
        ok: false,
        status: 401,
        json: async () => ({}),
      } as unknown as Response);
    });

    await expect(getInboxSummary()).rejects.toThrow();
    // Der alte Request darf die frisch angemeldete Sitzung nicht ausgeloggt haben.
    expect(getAccessToken()).toBe("new-access");
    expect(localStorage.getItem(REFRESH_KEY)).toBe("new-refresh");
  });

  it("ein veralteter Request wird nach Neuanmeldung NICHT mit der neuen Identität wiederholt", async () => {
    localStorage.setItem(ACCESS_KEY, "old-access");
    localStorage.setItem(REFRESH_KEY, "old-refresh");

    let apiCalls = 0;
    let apiRetried = false;
    let refreshAttempted = false;

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/token/") && !url.includes("refresh")) {
        // Login der neuen Sitzung (erhöht die Generation, setzt neue Tokens).
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ access: "new-access", refresh: "new-refresh" }),
        } as unknown as Response);
      }
      if (url.includes("/auth/token/refresh/")) {
        // Darf für den veralteten Request GAR NICHT erst versucht werden.
        refreshAttempted = true;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ access: "stale-access" }),
        } as unknown as Response);
      }
      // Eigentlicher API-Request (getInboxSummary).
      apiCalls += 1;
      if (apiCalls === 1) {
        // Während des ersten Versuchs meldet sich ein neuer Nutzer an.
        return login("neu", "pw").then(
          () =>
            ({ ok: false, status: 401, json: async () => ({}) }) as unknown as Response,
        );
      }
      // Ein ZWEITER API-Call wäre der Retry unter der neuen Identität → verboten.
      apiRetried = true;
      return Promise.resolve({
        ok: false,
        status: 401,
        json: async () => ({}),
      } as unknown as Response);
    });

    await expect(getInboxSummary()).rejects.toThrow();
    // Kein Refresh und kein Retry unter der neuen Sitzung.
    expect(refreshAttempted).toBe(false);
    expect(apiRetried).toBe(false);
    expect(apiCalls).toBe(1);
    // Die neue Anmeldung bleibt unangetastet.
    expect(getAccessToken()).toBe("new-access");
    expect(localStorage.getItem(REFRESH_KEY)).toBe("new-refresh");
  });
});
