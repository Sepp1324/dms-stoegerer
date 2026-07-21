import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DetailPreview } from "./DetailPreview";

// Vorschau-iframe: Chromes nativer PDF-Viewer rendert NUR in einem komplett
// un-sandboxed iframe – jede Sandbox-Variante (auch allow-scripts+allow-same-
// origin+allow-downloads) ließ die PDF-Vorschau leer (im Browser verifiziert).
// Daher trägt das iframe bewusst KEIN sandbox-Attribut.
//
// Sicherheit sitzt eine Ebene davor: Die Magic-Byte-Allowlist beim Ingest lässt
// nur echte PDFs/Raster-Bilder in den Bestand (HTML/SVG/XML werden abgewiesen),
// is_safe_inline erzwingt für alles andere Download (SVG explizit ausgeschlossen)
// und die Auslieferung setzt nosniff + expliziten Content-Type. Dieser Test
// hält das iframe sandbox-frei, damit nicht versehentlich wieder eine (nicht
// rendernde) Sandbox "gehärtet" wird und die Vorschau erneut leer bleibt.
describe("DetailPreview – iframe sandbox", () => {
  it("rendert das Vorschau-iframe OHNE sandbox (sonst leere PDF-Vorschau)", () => {
    render(<DetailPreview pdfUrl="blob:test-url" pdfError={null} title="Rechnung" />);
    const frame = screen.getByTitle("Vorschau: Rechnung");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame.getAttribute("src")).toBe("blob:test-url");
    expect(frame.hasAttribute("sandbox")).toBe(false);
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
