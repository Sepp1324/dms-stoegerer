import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAccessToken, getInboxSummary, logout } from "./api";

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
});
