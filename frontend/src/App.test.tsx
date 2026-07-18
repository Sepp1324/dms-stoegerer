import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import App from "./App";

// #7 Stage 1: echtes Routing. Nicht angemeldet → keine Datenrequests
// (DocumentsPage wird nicht gemountet), daher sichere Smoke-Tests der Routen.
describe("App-Routing (#7 Stage 1)", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("rendert den Login auf der Haupt-Route /", () => {
    window.history.pushState({}, "", "/");
    render(<App />);
    expect(screen.getByRole("button", { name: /Anmelden/i })).toBeInTheDocument();
  });

  it("matcht /share/:token und zeigt den Freigabe-Anmeldehinweis", () => {
    window.history.pushState({}, "", "/share/tok123");
    render(<App />);
    expect(screen.getByText(/geteilte Dokument/i)).toBeInTheDocument();
  });
});
