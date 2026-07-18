// Vitest-Setup (#9): erweitert Vitests `expect` um die jest-dom-Matcher
// (z. B. toBeInTheDocument) inkl. Typen – wird über test.setupFiles geladen.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Bei globals:false registriert Testing-Library kein automatisches Cleanup –
// sonst akkumulieren gerenderte Komponenten über Tests hinweg im document.body.
afterEach(() => {
  cleanup();
});
