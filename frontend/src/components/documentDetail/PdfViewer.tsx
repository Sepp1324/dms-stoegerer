import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";

// PDF.js-Viewer (Phase 0 des Dokument-Studios). Ersetzt den nativen PDF-iframe:
// gerendert wird auf Canvas – KEIN iframe, KEIN Blob-Frame, KEIN CSP/frame-
// ancestors-Konflikt (das beseitigt die wiederkehrende Vorschau-Bug-Klasse).
// Sicherheit: PDF.js führt PDF-eigenes JavaScript per Default NICHT aus
// (enableScripting/isEvalSupported aus); die XSS-Abwehr (Magic-Byte-Allowlist +
// Content-Type + nosniff) bleibt zusätzlich im Backend. Der einbettbare Textlayer
// macht den PDF-Text auswählbar (Grundlage für das spätere OCR-Overlay).

// Worker über Vite bündeln (import.meta.url -> gehashtes Asset).
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const THUMB_WIDTH = 120;
const MIN_SCALE = 0.4;
const MAX_SCALE = 4;
const SCALE_STEP = 0.2;

function clampScale(s: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.round(s * 100) / 100));
}

// Lazy gerendertes Seiten-Thumbnail: rendert erst, wenn es in den Sichtbereich der
// Seitenleiste scrollt (große PDFs bleiben performant).
function ThumbItem({
  pageNumber,
  active,
  onSelect,
}: {
  pageNumber: number;
  active: boolean;
  onSelect: (n: number) => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || visible) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          io.disconnect();
        }
      },
      { rootMargin: "300px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [visible]);

  return (
    <button
      ref={ref}
      type="button"
      className={`pdf-thumb${active ? " pdf-thumb--active" : ""}`}
      onClick={() => onSelect(pageNumber)}
      aria-label={`Seite ${pageNumber}`}
      aria-current={active ? "true" : undefined}
    >
      <span className="pdf-thumb__frame">
        {visible ? (
          <Page
            pageNumber={pageNumber}
            width={THUMB_WIDTH}
            renderTextLayer={false}
            renderAnnotationLayer={false}
            loading={<span className="pdf-thumb__ph" />}
          />
        ) : (
          <span className="pdf-thumb__ph" />
        )}
      </span>
      <span className="pdf-thumb__no">{pageNumber}</span>
    </button>
  );
}

export function PdfViewer({
  url,
  title,
  initialPage,
}: {
  url: string;
  title: string;
  initialPage?: number | null;
}) {
  const [numPages, setNumPages] = useState<number | null>(null);
  const [page, setPage] = useState(initialPage && initialPage > 0 ? initialPage : 1);
  const [scale, setScale] = useState(1.2);
  const [loadError, setLoadError] = useState<string | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);

  // NUR bei echtem Dokumentwechsel (neue URL) das geladene PDF verwerfen. Bewusst
  // NICHT an initialPage gekoppelt: sonst würde eine nachträgliche initialPage-
  // Änderung numPages nullen, ohne dass <Document> (gleiche file-URL) onLoadSuccess
  // erneut feuert – die Folge wären fehlende Thumbnails und deaktivierte Navigation.
  useEffect(() => {
    setNumPages(null);
    setLoadError(null);
  }, [url]);

  // Sprungseite (Deep-Link) setzen – bei Dokumentwechsel wie bei nachträglicher
  // initialPage-Änderung. Die harte Begrenzung auf gültige Seiten folgt nach dem Laden.
  useEffect(() => {
    setPage(initialPage && initialPage > 0 ? initialPage : 1);
  }, [url, initialPage]);

  // Nach dem Laden die aktuelle Seite hart auf [1..numPages] klemmen: eine zu große
  // (oder aus einem vorigen PDF stammende) initialPage würde sonst eine ungültige
  // Seite rendern und die Navigation blockieren.
  useEffect(() => {
    if (numPages) setPage((p) => Math.min(numPages, Math.max(1, p)));
  }, [numPages]);

  const goTo = (n: number) => {
    if (!numPages) return;
    setPage(Math.min(numPages, Math.max(1, n)));
  };

  // Tastaturnavigation NUR innerhalb des Viewers (Handler am fokussierbaren Stage-Div,
  // nicht global auf window). Sonst würden Pfeiltasten/Home/End/+/- auch bei Fokus auf
  // Tabs oder anderen Buttons die Seite umschalten und normales Scrollen verhindern.
  // Eingabefelder (Seitennummer) werden ausgenommen, damit Tippen nicht kollidiert.
  const onStageKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const t = e.target as HTMLElement | null;
    if (
      t &&
      (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)
    ) {
      return;
    }
    switch (e.key) {
      case "ArrowRight":
      case "PageDown":
        setPage((p) => (numPages ? Math.min(numPages, p + 1) : p));
        break;
      case "ArrowLeft":
      case "PageUp":
        setPage((p) => Math.max(1, p - 1));
        break;
      case "Home":
        setPage(1);
        break;
      case "End":
        if (numPages) setPage(numPages);
        break;
      case "+":
      case "=":
        setScale((s) => clampScale(s + SCALE_STEP));
        break;
      case "-":
        setScale((s) => clampScale(s - SCALE_STEP));
        break;
      case "0":
        setScale(1.2);
        break;
      default:
        return;
    }
    e.preventDefault();
  };

  const pageNumbers = useMemo(
    () => (numPages ? Array.from({ length: numPages }, (_, i) => i + 1) : []),
    [numPages],
  );

  if (loadError) {
    return <p className="status status--warn">Vorschau: {loadError}</p>;
  }

  return (
    <Document
      file={url}
      onLoadSuccess={({ numPages: n }) => setNumPages(n)}
      onLoadError={(err) => setLoadError(err.message || "PDF konnte nicht geladen werden.")}
      loading={<p className="muted">Lade Vorschau …</p>}
      error={<p className="status status--warn">Vorschau konnte nicht geladen werden.</p>}
      className="pdf-viewer"
    >
      <aside className="pdf-thumbs" aria-label="Seitenübersicht">
        {pageNumbers.map((n) => (
          <ThumbItem key={n} pageNumber={n} active={n === page} onSelect={goTo} />
        ))}
      </aside>

      <div
        className="pdf-stage"
        ref={stageRef}
        role="group"
        aria-label={`PDF-Vorschau: ${title} (Pfeiltasten blättern, +/− zoomen)`}
        tabIndex={0}
        onKeyDown={onStageKeyDown}
      >
        <div className="pdf-toolbar">
          <div className="pdf-toolbar__group">
            <button
              type="button"
              onClick={() => goTo(page - 1)}
              disabled={page <= 1}
              aria-label="Vorherige Seite"
            >
              ‹
            </button>
            <span className="pdf-toolbar__pages">
              <input
                type="number"
                min={1}
                max={numPages ?? 1}
                value={page}
                onChange={(e) => goTo(Number(e.target.value) || 1)}
                aria-label="Seite"
              />
              <span> / {numPages ?? "…"}</span>
            </span>
            <button
              type="button"
              onClick={() => goTo(page + 1)}
              disabled={!numPages || page >= numPages}
              aria-label="Nächste Seite"
            >
              ›
            </button>
          </div>
          <div className="pdf-toolbar__group">
            <button
              type="button"
              onClick={() => setScale((s) => clampScale(s - SCALE_STEP))}
              aria-label="Verkleinern"
            >
              −
            </button>
            <span className="pdf-toolbar__zoom">{Math.round(scale * 100)}%</span>
            <button
              type="button"
              onClick={() => setScale((s) => clampScale(s + SCALE_STEP))}
              aria-label="Vergrößern"
            >
              +
            </button>
            <button type="button" onClick={() => setScale(1.2)} aria-label="Zoom zurücksetzen">
              Reset
            </button>
          </div>
        </div>

        <div className="pdf-canvas-wrap">
          <Page
            key={page}
            pageNumber={page}
            scale={scale}
            renderTextLayer
            renderAnnotationLayer={false}
            loading={<p className="muted">Lade Seite …</p>}
            canvasBackground="#fff"
            className="pdf-page"
          />
        </div>
      </div>
    </Document>
  );
}
