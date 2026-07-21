import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DetailPreview } from "./DetailPreview";

// Vorschau-Sandbox: Chromes nativer PDF-Viewer rendert im iframe NUR mit
// allow-scripts UND allow-same-origin – fehlt eines, bleibt die PDF-Vorschau
// komplett leer (verifiziert im Browser). Die frühere P0-2-Variante ohne diese
// Flags brach daher jede PDF-Vorschau.
//
// Die XSS-Härtung ist deshalb eine Ebene nach vorn gezogen (nicht mehr der
// iframe-sandbox): Die Magic-Byte-Allowlist beim Ingest lässt NUR echte PDFs/
// Bilder in den Bestand, und die Auslieferung setzt Content-Type=application/pdf
// + X-Content-Type-Options=nosniff. Dieser Test zementiert die render-fähige
// Sandbox, damit sie nicht versehentlich wieder auf einen leeren Wert
// "gehärtet" wird.
describe("DetailPreview – iframe sandbox", () => {
  it("erlaubt allow-scripts + allow-same-origin + allow-downloads (sonst leere PDF-Vorschau)", () => {
    render(<DetailPreview pdfUrl="blob:test-url" pdfError={null} title="Rechnung" />);
    const frame = screen.getByTitle("Vorschau: Rechnung");
    expect(frame.tagName).toBe("IFRAME");
    const tokens = (frame.getAttribute("sandbox") ?? "").split(/\s+/);
    expect(tokens).toContain("allow-scripts");
    expect(tokens).toContain("allow-same-origin");
    expect(tokens).toContain("allow-downloads");
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
