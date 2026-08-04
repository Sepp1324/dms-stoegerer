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
const getPageLayoutPage = vi.fn();
vi.mock("../../api", () => ({
  getPageLayoutPage: (...args: unknown[]) => getPageLayoutPage(...args),
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
  getPageLayoutPage.mockReset();
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

describe("PdfViewer – OCR-Overlay / Suche", () => {
  it("zeigt keine Suche ohne docId (z. B. öffentliche Freigabe)", () => {
    render(<PdfViewer url="blob:a" title="Doc" />);
    load(5);
    expect(screen.queryByLabelText("Im Dokument suchen")).toBeNull();
    expect(getPageLayoutPage).not.toHaveBeenCalled();
  });

  it("lädt die Wortliste und hebt Treffer hervor", async () => {
    getPageLayoutPage.mockResolvedValue({
      document: 7,
      version_id: 1,
      version_no: 1,
      page_count: 1,
      page: {
        page_no: 1,
        width: 100,
        height: 100,
        words: [
          { t: "Rechnung", bbox: [10, 20, 30, 40] },
          { t: "Müller", bbox: [50, 60, 70, 80] },
        ],
      },
    });

    render(<PdfViewer url="blob:a" title="Doc" docId={7} layoutVersion={null} />);
    load(1);
    renderPage(200, 200); // rendered-Maße setzen (Overlay-Bezug)

    fireEvent.change(screen.getByLabelText("Im Dokument suchen"), {
      target: { value: "muller" },
    });

    await waitFor(() =>
      expect(getPageLayoutPage).toHaveBeenCalledWith(7, 1, null, expect.anything()),
    );
    // Genau ein Treffer (Müller, diakritika-tolerant) als Overlay-Kasten.
    await waitFor(() => {
      const overlay = document.querySelector(".ocr-overlay");
      expect(overlay).not.toBeNull();
      expect(overlay?.querySelectorAll(".ocr-word--match").length).toBe(1);
    });
    expect(screen.getByRole("status")).toHaveTextContent("1 Treffer auf Seite 1");
  });

  it("meldet Seiten ohne Textebene weich (kein Overlay)", async () => {
    getPageLayoutPage.mockRejectedValue(new Error("HTTP 404"));

    render(<PdfViewer url="blob:a" title="Doc" docId={7} layoutVersion={null} />);
    load(1);
    renderPage(200, 200);
    fireEvent.change(screen.getByLabelText("Im Dokument suchen"), {
      target: { value: "abc" },
    });

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        /keine durchsuchbare Textebene/i,
      ),
    );
    expect(document.querySelector(".ocr-word--match")).toBeNull();
  });
});
