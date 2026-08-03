import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import ErrorBoundary from "./components/ErrorBoundary";

// Selbstheilende Boundary: fängt Render-/Chunk-Load-Fehler ab. Der Auto-Reload
// beim ERSTEN Fehler wird über den Zeitstempel im sessionStorage getestet – setzen
// wir ihn auf "gerade eben", greift der Loop-Schutz und der Dialog erscheint.

const RECOVERY_KEY = "dms_recovery_ts";

function Boom(): JSX.Element {
  throw new Error("ChunkLoadError: Loading chunk 7 failed");
}

beforeEach(() => {
  sessionStorage.clear();
  // console.error der abgefangenen Fehler stummschalten (React + Boundary loggen).
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("ErrorBoundary", () => {
  it("rendert Kinder normal, wenn kein Fehler auftritt", () => {
    render(
      <ErrorBoundary>
        <p>alles ok</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("alles ok")).toBeInTheDocument();
  });

  it("zeigt den Wiederherstellungs-Dialog, wenn gerade erst neu geladen wurde", () => {
    // Kürzlicher Recovery-Zeitstempel -> Loop-Schutz -> KEIN erneuter Auto-Reload,
    // stattdessen der Dialog.
    sessionStorage.setItem(RECOVERY_KEY, String(Date.now()));
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/konnte nicht geladen werden/i)).toBeInTheDocument();
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(2);
    expect(screen.getByText("Neu laden")).toBeInTheDocument();
    expect(screen.getByText(/Abmelden/i)).toBeInTheDocument();
  });
});
