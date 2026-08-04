import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// react-pdf braucht Worker + Canvas (Browser). Hier wird es durch schlanke
// Stand-ins ersetzt, damit die Viewer-LOGIK (Startseite, Seitenbegrenzung,
// fokusgebundene Hotkeys, Zustand bei initialPage-Wechsel) in jsdom prüfbar wird –
// genau die zuvor korrigierten Fälle, die der DetailPreview-Test (PdfViewer gemockt)
// nicht abdeckt.
const h = vi.hoisted(() => ({ onLoad: null as null | ((p: { numPages: number }) => void) }));

vi.mock("react-pdf", () => ({
  pdfjs: { GlobalWorkerOptions: {} },
  Document: ({
    children,
    onLoadSuccess,
  }: {
    children: React.ReactNode;
    onLoadSuccess?: (p: { numPages: number }) => void;
  }) => {
    h.onLoad = onLoadSuccess ?? null;
    return <div data-testid="document">{children}</div>;
  },
  Page: ({ pageNumber }: { pageNumber: number }) => (
    <div data-testid="main-page" data-page={pageNumber} />
  ),
}));

import { PdfViewer } from "./PdfViewer";

// IntersectionObserver fehlt in jsdom; No-op-Stub, der NIE feuert → Thumbnails
// bleiben Platzhalter (kein zweites <Page>), die Testlogik bleibt einfach.
class IOStub {
  observe() {}
  disconnect() {}
  unobserve() {}
}

beforeEach(() => {
  h.onLoad = null;
  // @ts-expect-error – Test-Stub
  globalThis.IntersectionObserver = IOStub;
});

function load(numPages: number) {
  act(() => h.onLoad?.({ numPages }));
}

const mainPageNo = () =>
  Number(screen.getByTestId("main-page").getAttribute("data-page"));

const stage = () => screen.getByRole("group", { name: /PDF-Vorschau/ });

describe("PdfViewer – Logik (echter Viewer, react-pdf gemockt)", () => {
  it("startet auf initialPage", () => {
    render(<PdfViewer url="blob:a" title="Doc" initialPage={3} />);
    load(5);
    expect(mainPageNo()).toBe(3);
  });

  it("klemmt eine zu große initialPage nach dem Laden auf numPages", () => {
    render(<PdfViewer url="blob:a" title="Doc" initialPage={99} />);
    load(5);
    expect(mainPageNo()).toBe(5);
  });

  it("blättert per Hotkey NUR bei Fokus im Viewer", () => {
    render(<PdfViewer url="blob:a" title="Doc" />);
    load(5);
    expect(mainPageNo()).toBe(1);

    // Innerhalb des Viewers: ArrowRight blättert vor.
    fireEvent.keyDown(stage(), { key: "ArrowRight" });
    expect(mainPageNo()).toBe(2);

    // Außerhalb (document.body): darf den Viewer NICHT beeinflussen.
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    expect(mainPageNo()).toBe(2);
  });

  it("ignoriert Hotkeys im Seitennummer-Eingabefeld", () => {
    render(<PdfViewer url="blob:a" title="Doc" />);
    load(5);
    const input = screen.getByLabelText("Seite");
    fireEvent.keyDown(input, { key: "ArrowRight" });
    expect(mainPageNo()).toBe(1); // unverändert
  });

  it("behält numPages bei bloßer initialPage-Änderung (Thumbnails bleiben)", () => {
    const { rerender } = render(
      <PdfViewer url="blob:a" title="Doc" initialPage={1} />,
    );
    load(5);
    const thumbs = () =>
      screen.getAllByRole("button", { name: /^Seite \d+$/ }).length;
    expect(thumbs()).toBe(5);

    // Gleiche URL, neue Startseite: numPages darf NICHT genullt werden (sonst
    // wäre <Document> nicht neu geladen und die Thumbnails verschwänden).
    rerender(<PdfViewer url="blob:a" title="Doc" initialPage={4} />);
    expect(thumbs()).toBe(5);
    expect(mainPageNo()).toBe(4);
  });
});
