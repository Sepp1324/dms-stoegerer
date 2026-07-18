import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DetailPreview } from "./DetailPreview";

// Regressions-Guard für den P0-2-XSS-Fix: die Vorschau MUSS in einem sandboxed
// iframe laufen, und der Sandbox darf NIE allow-scripts + allow-same-origin
// zugleich erlauben (sonst könnte durchgerutschtes HTML/SVG auf localStorage/
// Cookies des DMS zugreifen).
describe("DetailPreview – iframe sandbox (P0-2)", () => {
  it("rendert die Vorschau in einem sandboxed iframe ohne allow-scripts/allow-same-origin", () => {
    render(<DetailPreview pdfUrl="blob:test-url" pdfError={null} title="Rechnung" />);
    const frame = screen.getByTitle("Vorschau: Rechnung");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame.hasAttribute("sandbox")).toBe(true);
    const sandbox = frame.getAttribute("sandbox") ?? "";
    expect(sandbox).not.toContain("allow-scripts");
    expect(sandbox).not.toContain("allow-same-origin");
  });

  it("zeigt bei Fehler eine Warnung und KEIN iframe", () => {
    render(<DetailPreview pdfUrl={null} pdfError="Datei fehlt" title="X" />);
    expect(screen.getByText(/Vorschau: Datei fehlt/)).toBeInTheDocument();
    expect(screen.queryByTitle(/Vorschau:/)).toBeNull();
  });

  it("zeigt einen Ladehinweis ohne URL und ohne Fehler", () => {
    render(<DetailPreview pdfUrl={null} pdfError={null} title="X" />);
    expect(screen.getByText(/Lade Vorschau/)).toBeInTheDocument();
  });
});
