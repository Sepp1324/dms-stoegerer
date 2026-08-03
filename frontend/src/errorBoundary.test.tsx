import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import ErrorBoundary from "./components/ErrorBoundary";

// Selbstheilende Boundary: NUR echte Chunk-Load-Fehler laden automatisch neu.
// Normale Programmierfehler zeigen sofort den Dialog (kein Reload -> keine
// verlorenen Eingaben). Der Auto-Reload wird über den Zeitstempel loop-geschützt.

const RECOVERY_KEY = "dms_recovery_ts";

function ChunkBoom(): JSX.Element {
  throw new Error("ChunkLoadError: Loading chunk 7 failed");
}
function CodeBoom(): JSX.Element {
  throw new Error("Cannot read properties of undefined (reading 'title')");
}

let reloadSpy: ReturnType<typeof vi.fn>;
const realLocation = window.location;

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(console, "error").mockImplementation(() => {});
  reloadSpy = vi.fn();
  // jsdom erlaubt kein Überschreiben von location.reload direkt -> location als
  // Ganzes durch einen minimalen Stub ersetzen.
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { href: "http://localhost/", reload: reloadSpy, assign: vi.fn() },
  });
});
afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: realLocation,
  });
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

  it("lädt bei einem frischen Chunk-Load-Fehler automatisch neu", () => {
    render(
      <ErrorBoundary>
        <ChunkBoom />
      </ErrorBoundary>,
    );
    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });

  it("lädt bei einem normalen Programmierfehler NICHT neu, sondern zeigt den Dialog", () => {
    render(
      <ErrorBoundary>
        <CodeBoom />
      </ErrorBoundary>,
    );
    expect(reloadSpy).not.toHaveBeenCalled();
    expect(screen.getByText(/Fehler aufgetreten/i)).toBeInTheDocument();
    // Kein Update-spezifischer Text bei einem echten Code-Fehler.
    expect(screen.getByText(/setzt die Seite zurück/i)).toBeInTheDocument();
  });

  it("zeigt bei erneutem Chunk-Fehler im Loop-Fenster den Dialog statt Reload", () => {
    sessionStorage.setItem(RECOVERY_KEY, String(Date.now()));
    render(
      <ErrorBoundary>
        <ChunkBoom />
      </ErrorBoundary>,
    );
    expect(reloadSpy).not.toHaveBeenCalled();
    expect(screen.getByText(/Fehler aufgetreten/i)).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });
});
