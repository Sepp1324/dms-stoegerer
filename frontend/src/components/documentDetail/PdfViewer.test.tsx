import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// react-pdf braucht Worker + Canvas (Browser). Hier wird es durch schlanke
// Stand-ins ersetzt, damit die Viewer-LOGIK (Startseite, Seitenbegrenzung,
// fokusgebundene Hotkeys, Zustand bei initialPage-Wechsel, OCR-Overlay/Suche) in
// jsdom prüfbar wird. onLoadSuccess/onRenderSuccess werden als Handles exponiert
// und im Test via act() ausgelöst.
const h = vi.hoisted(() => ({
  onLoad: null as null | ((p: { numPages: number }) => void),
  onRender: null as null | ((p: { width: number; height: number }) => void),
}));

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
  Page: ({
    pageNumber,
    onRenderSuccess,
  }: {
    pageNumber: number;
    onRenderSuccess?: (p: { width: number; height: number }) => void;
  }) => {
    // Nur die Hauptseite reicht onRenderSuccess durch (Thumbnails nicht).
    if (onRenderSuccess) h.onRender = onRenderSuccess;
    return <div data-testid="main-page" data-page={pageNumber} />;
  },
}));

// page-layout-Endpoint mocken (die Suche lädt die Wortliste der aktuellen Seite).
const searchPageLayout = vi.fn();
vi.mock("../../api", () => ({
  searchPageLayout: (...args: unknown[]) => searchPageLayout(...args),
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
  h.onRender = null;
  searchPageLayout.mockReset();
  // scrollIntoView existiert in jsdom nicht.
  Element.prototype.scrollIntoView = vi.fn();
  // @ts-expect-error – Test-Stub
  globalThis.IntersectionObserver = IOStub;
});

function load(numPages: number) {
  act(() => h.onLoad?.({ numPages }));
}
function renderPage(width = 200, height = 200) {
  act(() => h.onRender?.({ width, height }));
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
    expect(mainPageNo()).toBe(1);
  });

  it("behält numPages bei bloßer initialPage-Änderung (Thumbnails bleiben)", () => {
    const { rerender } = render(
      <PdfViewer url="blob:a" title="Doc" initialPage={1} />,
    );
    load(5);
    const thumbs = () =>
      screen.getAllByRole("button", { name: /^Seite \d+$/ }).length;
    expect(thumbs()).toBe(5);

    rerender(<PdfViewer url="blob:a" title="Doc" initialPage={4} />);
    expect(thumbs()).toBe(5);
    expect(mainPageNo()).toBe(4);
  });
});

describe("PdfViewer – OCR-Overlay / seitenübergreifende Suche", () => {
  const search = (
    matches: {
      page_no: number;
      width: number;
      height: number;
      bbox: [number, number, number, number];
      t: string;
    }[],
    truncated = false,
  ) =>
    searchPageLayout.mockResolvedValue({
      document: 7,
      version_id: 1,
      version_no: 1,
      page_count: 2,
      total: matches.length,
      truncated,
      matches,
    });

  it("zeigt keine Suche ohne docId (z. B. öffentliche Freigabe)", () => {
    render(<PdfViewer url="blob:a" title="Doc" />);
    load(5);
    expect(screen.queryByLabelText("Im Dokument suchen")).toBeNull();
    expect(searchPageLayout).not.toHaveBeenCalled();
  });

  it("sucht serverseitig und markiert den aktiven Treffer", async () => {
    search([
      { page_no: 1, width: 100, height: 100, bbox: [10, 20, 30, 40], t: "Rechnung" },
      { page_no: 1, width: 100, height: 100, bbox: [50, 60, 70, 80], t: "Rechnungsnr" },
    ]);

    render(<PdfViewer url="blob:a" title="Doc" docId={7} layoutVersion={null} />);
    load(2);
    renderPage(200, 200);

    fireEvent.change(screen.getByLabelText("Im Dokument suchen"), {
      target: { value: "rechnung" },
    });

    await waitFor(() =>
      expect(searchPageLayout).toHaveBeenCalledWith(7, "rechnung", null, expect.anything()),
    );
    await waitFor(() => {
      const overlay = document.querySelector(".ocr-overlay");
      expect(overlay?.querySelectorAll(".ocr-word--match").length).toBe(2);
      expect(overlay?.querySelectorAll(".ocr-word--active").length).toBe(1);
    });
    expect(screen.getByRole("status")).toHaveTextContent("1/2");
  });

  it("springt mit »Nächster Treffer« seitenübergreifend", async () => {
    search([
      { page_no: 1, width: 100, height: 100, bbox: [10, 20, 30, 40], t: "Treffer1" },
      { page_no: 2, width: 100, height: 100, bbox: [10, 20, 30, 40], t: "Treffer2" },
    ]);

    render(<PdfViewer url="blob:a" title="Doc" docId={7} layoutVersion={null} />);
    load(2);
    renderPage(200, 200);
    fireEvent.change(screen.getByLabelText("Im Dokument suchen"), {
      target: { value: "treffer" },
    });

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("1/2"));
    expect(mainPageNo()).toBe(1);

    fireEvent.click(screen.getByLabelText("Nächster Treffer"));
    expect(mainPageNo()).toBe(2);
    renderPage(200, 200); // neue Seite rendert → Overlay-Bezug
    await waitFor(() => {
      expect(document.querySelector(".ocr-word--active")).not.toBeNull();
    });
    expect(screen.getByRole("status")).toHaveTextContent("2/2");
  });

  it("meldet »Keine Treffer« weich (kein Overlay)", async () => {
    search([]);
    render(<PdfViewer url="blob:a" title="Doc" docId={7} layoutVersion={null} />);
    load(1);
    renderPage(200, 200);
    fireEvent.change(screen.getByLabelText("Im Dokument suchen"), {
      target: { value: "zzz" },
    });

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/keine treffer/i),
    );
    expect(document.querySelector(".ocr-word--match")).toBeNull();
  });

  it("meldet Endpoint-Fehler weich", async () => {
    searchPageLayout.mockRejectedValue(new Error("HTTP 500"));
    render(<PdfViewer url="blob:a" title="Doc" docId={7} layoutVersion={null} />);
    load(1);
    renderPage(200, 200);
    fireEvent.change(screen.getByLabelText("Im Dokument suchen"), {
      target: { value: "abc" },
    });

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/nicht verfügbar/i),
    );
    expect(document.querySelector(".ocr-word--match")).toBeNull();
  });
});
