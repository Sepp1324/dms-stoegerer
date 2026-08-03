import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DetailPreview } from "./DetailPreview";

// Der echte PDF.js-Viewer (Canvas/Worker) gehört in den Browser-Test; hier wird er
// gestubbt, um die Verzweigung PDF vs. Bild vs. Fehler/Laden zu prüfen.
vi.mock("./PdfViewer", () => ({
  PdfViewer: (props: { url: string; initialPage?: number | null }) => (
    <div data-testid="pdf-viewer" data-url={props.url} data-page={props.initialPage ?? ""} />
  ),
}));

describe("DetailPreview – Verzweigung", () => {
  it("rendert bei application/pdf den PDF.js-Viewer (kein iframe)", () => {
    render(
      <DetailPreview
        pdfUrl="blob:test-url"
        mime="application/pdf"
        pdfError={null}
        title="Rechnung"
        initialPage={3}
      />,
    );
    const viewer = screen.getByTestId("pdf-viewer");
    expect(viewer).toHaveAttribute("data-url", "blob:test-url");
    expect(viewer).toHaveAttribute("data-page", "3");
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("rendert Raster-Bilder als <img>", () => {
    render(
      <DetailPreview
        pdfUrl="blob:img-url"
        mime="image/jpeg"
        pdfError={null}
        title="Scan"
      />,
    );
    const img = screen.getByAltText("Vorschau: Scan");
    expect(img.tagName).toBe("IMG");
    expect(img).toHaveAttribute("src", "blob:img-url");
    expect(screen.queryByTestId("pdf-viewer")).toBeNull();
  });

  it("zeigt bei Fehler eine Warnung und keine Vorschau", () => {
    render(
      <DetailPreview pdfUrl={null} mime={null} pdfError="Datei fehlt" title="X" />,
    );
    expect(screen.getByText(/Vorschau: Datei fehlt/)).toBeInTheDocument();
    expect(screen.queryByTestId("pdf-viewer")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
  });

  it("zeigt einen Ladehinweis ohne URL und ohne Fehler", () => {
    render(<DetailPreview pdfUrl={null} mime={null} pdfError={null} title="X" />);
    expect(screen.getByText(/Lade Vorschau/)).toBeInTheDocument();
  });
});
