import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// P2: Der vor-atomare Auth-State (drei Einzel-Keys) wird beim Modul-Start einmalig
// atomar nach dms_auth_state migriert und die Alt-Keys entfernt – sonst würde der
// Deploy alle Nutzer ausloggen. Die Migration läuft im Modul-Init, daher via
// resetModules + dynamischem Import getestet.

beforeEach(() => {
  localStorage.clear();
  vi.resetModules();
});
afterEach(() => {
  localStorage.clear();
});

describe("Legacy-Auth-Migration", () => {
  it("überführt vollständige Alt-Keys atomar und entfernt sie", async () => {
    localStorage.setItem("dms_access", "acc");
    localStorage.setItem("dms_refresh", "ref");
    localStorage.setItem("dms_session", "sess");

    await import("./api"); // Modul-Init ruft migrateLegacyAuth()

    expect(JSON.parse(localStorage.getItem("dms_auth_state")!)).toEqual({
      session: "sess",
      access: "acc",
      refresh: "ref",
    });
    expect(localStorage.getItem("dms_access")).toBeNull();
    expect(localStorage.getItem("dms_refresh")).toBeNull();
    expect(localStorage.getItem("dms_session")).toBeNull();
  });

  it("räumt unvollständige Alt-Keys auf, ohne einen kaputten State anzulegen", async () => {
    localStorage.setItem("dms_access", "acc"); // refresh fehlt

    await import("./api");

    expect(localStorage.getItem("dms_auth_state")).toBeNull();
    expect(localStorage.getItem("dms_access")).toBeNull();
  });

  it("lässt einen bereits vorhandenen neuen State unangetastet", async () => {
    localStorage.setItem(
      "dms_auth_state",
      JSON.stringify({ session: "s", access: "a", refresh: "r" }),
    );
    localStorage.setItem("dms_access", "alt"); // Rest eines Alt-Zustands

    await import("./api");

    expect(JSON.parse(localStorage.getItem("dms_auth_state")!).access).toBe("a");
    // Alt-Keys werden trotzdem bereinigt.
    expect(localStorage.getItem("dms_access")).toBeNull();
  });
});
