// Vitest-Setup (#9): erweitert Vitests `expect` um die jest-dom-Matcher
// (z. B. toBeInTheDocument) inkl. Typen – wird über test.setupFiles geladen.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Node >= 22 definiert auf globalThis eigene (lazy) Getter für localStorage und
// sessionStorage (Web Storage API). Ohne `--localstorage-file` liefern sie
// `undefined` – und weil Vitests jsdom-Umgebung nur Keys auf das Global kopiert,
// die dort NICHT schon existieren, verdecken sie die jsdom-Storages. Folge unter
// Node 22+/26: `localStorage.getItem` → "Cannot read properties of undefined".
// Vitest setzt `window = globalThis`, die echte jsdom-Window-Instanz hängt aber
// als `globalThis.jsdom.window` dran – von dort die Storages übernehmen.
// Unter Node 20 (kein eigener Getter) greift die Bedingung nicht (No-op).
{
  const jsdomWindow = (globalThis as { jsdom?: { window?: Window } }).jsdom?.window;
  for (const key of ["localStorage", "sessionStorage"] as const) {
    const storage = jsdomWindow?.[key];
    if (storage && globalThis[key] !== storage) {
      Object.defineProperty(globalThis, key, {
        value: storage,
        configurable: true,
        writable: true,
        enumerable: true,
      });
    }
  }
}

// Bei globals:false registriert Testing-Library kein automatisches Cleanup –
// sonst akkumulieren gerenderte Komponenten über Tests hinweg im document.body.
afterEach(() => {
  cleanup();
});
