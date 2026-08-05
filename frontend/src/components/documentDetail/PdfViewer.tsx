import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";

import {
  getPageLayoutMeta,
  searchPageLayout,
  type PageLayoutMatch,
} from "../../api";
import { OcrOverlay } from "./OcrOverlay";
import type { Bbox } from "./overlayGeometry";

// Sprungziel aus dem Beleg-Daten-Panel: Seite + Fundstellen-Box. ``nonce`` erlaubt
// erneutes Anspringen desselben Ziels (der Effekt reagiert auf die Änderung).
export interface PdfHighlight {
  page: number;
  bbox: Bbox;
  nonce: number;
}

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
  docId,
  layoutVersion,
  highlight,
}: {
  url: string;
  title: string;
  initialPage?: number | null;
  // Nur gesetzt in der Detailansicht (Owner/Haushalt): aktiviert das OCR-Overlay
  // via page-layout-Endpoint. Ohne docId (z. B. öffentliche Freigabe) bleibt das
  // Overlay/die Suche aus. layoutVersion = version_no der gerade gezeigten Version.
  docId?: number;
  layoutVersion?: number | null;
  // Sprungziel aus dem Beleg-Daten-Panel (Fundstelle einer Extraktion markieren).
  highlight?: PdfHighlight | null;
}) {
  const [numPages, setNumPages] = useState<number | null>(null);
  const [page, setPage] = useState(initialPage && initialPage > 0 ? initialPage : 1);
  const [scale, setScale] = useState(1.2);
  const [loadError, setLoadError] = useState<string | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);

  // --- OCR-Overlay / seitenübergreifende In-Dokument-Suche (Phase 2) ---
  const [query, setQuery] = useState("");
  // Gerenderte Seitengröße (CSS-px) aus react-pdf onRenderSuccess – Bezug fürs Overlay.
  const [rendered, setRendered] = useState<{ w: number; h: number } | null>(null);
  const [matches, setMatches] = useState<PageLayoutMatch[]>([]);
  const [activeMatch, setActiveMatch] = useState(-1); // Index in matches (-1 = keiner)
  const [truncated, setTruncated] = useState(false);
  const [searchMsg, setSearchMsg] = useState("");

  // Gerenderte Größe bei Seiten-/Zoomwechsel verwerfen: das Overlay bleibt aus, bis
  // die neue Seite gerendert und onRenderSuccess die aktuellen Maße geliefert hat
  // (sonst säßen Treffer kurz an alten Koordinaten).
  useEffect(() => {
    setRendered(null);
  }, [page, scale, url]);

  // Suche zurücksetzen, wenn Dokument oder Version wechselt.
  useEffect(() => {
    setQuery("");
    setMatches([]);
    setActiveMatch(-1);
    setTruncated(false);
    setSearchMsg("");
  }, [url, docId, layoutVersion]);

  const trimmedQuery = query.trim();

  // Seitenübergreifende Suche (debounced), serverseitig gematcht. Springt zum ersten
  // Treffer. Ohne docId (öffentliche Freigabe) oder leere Eingabe bleibt alles leer.
  useEffect(() => {
    if (!docId || !trimmedQuery) {
      setMatches([]);
      setActiveMatch(-1);
      setTruncated(false);
      setSearchMsg("");
      return;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => {
      searchPageLayout(docId, trimmedQuery, layoutVersion, ctrl.signal)
        .then((res) => {
          setMatches(res.matches);
          setTruncated(res.truncated);
          setSearchMsg(res.total === 0 ? "Keine Treffer." : "");
          setActiveMatch(res.matches.length ? 0 : -1);
          if (res.matches.length) setPage(res.matches[0].page_no);
        })
        .catch(() => {
          if (ctrl.signal.aborted) return;
          // Weich: keine Textebene / Endpoint-Fehler stört den Viewer nicht.
          setMatches([]);
          setActiveMatch(-1);
          setTruncated(false);
          setSearchMsg("Suche nicht verfügbar (keine Textebene?).");
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [docId, layoutVersion, trimmedQuery]);

  // Treffer der aktuellen Seite + Index des aktiven Treffers DARIN (für die Markierung).
  const pageMatches = useMemo(
    () => matches.filter((m) => m.page_no === page),
    [matches, page],
  );
  const activeOnPage =
    activeMatch >= 0 && matches[activeMatch]?.page_no === page
      ? pageMatches.indexOf(matches[activeMatch])
      : -1;
  const overlayLayout = pageMatches[0];

  // Zum n-ten Treffer springen (mit Umlauf) und dessen Seite anzeigen.
  const goToMatch = (index: number) => {
    if (!matches.length) return;
    const wrapped = ((index % matches.length) + matches.length) % matches.length;
    setActiveMatch(wrapped);
    setPage(matches[wrapped].page_no);
  };

  // --- Beleg-Daten-Sprung: Fundstelle einer Extraktion markieren ---
  // Seitenmaße der Highlight-Seite (Bezug fürs Skalieren der Fundstellen-Box).
  const [hlDims, setHlDims] = useState<{ width: number; height: number } | null>(null);
  const hlNonce = highlight?.nonce ?? null;
  useEffect(() => {
    if (!docId || !highlight) {
      setHlDims(null);
      return;
    }
    setPage(highlight.page);
    const ctrl = new AbortController();
    getPageLayoutMeta(docId, layoutVersion, ctrl.signal)
      .then((meta) => {
        const p = meta.pages.find((x) => x.page_no === highlight.page);
        setHlDims(p ? { width: p.width, height: p.height } : null);
      })
      .catch(() => {
        if (!ctrl.signal.aborted) setHlDims(null);
      });
    return () => ctrl.abort();
    // Absichtlich auf nonce hören: erneuter Klick auf dasselbe Ziel springt wieder.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId, layoutVersion, hlNonce]);

  // Vereinheitlichtes Overlay: ein aktiver Fundstellen-Sprung auf der aktuellen Seite
  // hat Vorrang; sonst die Suchtreffer. So gibt es nie zwei konkurrierende Overlays.
  const highlightActive =
    !!docId &&
    !!highlight &&
    highlight.page === page &&
    !!hlDims &&
    !!rendered;
  const overlayBoxes: { bbox: Bbox }[] = highlightActive
    ? [{ bbox: highlight!.bbox }]
    : pageMatches;
  const overlayActiveIndex = highlightActive ? 0 : activeOnPage;
  const overlayDims = highlightActive
    ? hlDims
    : overlayLayout
      ? { width: overlayLayout.width, height: overlayLayout.height }
      : null;

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
          {docId && (
            <div className="pdf-toolbar__group pdf-toolbar__search">
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  // Enter = nächster Treffer, Shift+Enter = vorheriger.
                  if (e.key === "Enter" && matches.length) {
                    e.preventDefault();
                    goToMatch(activeMatch + (e.shiftKey ? -1 : 1));
                  }
                }}
                placeholder="Im Dokument suchen …"
                aria-label="Im Dokument suchen"
              />
              {trimmedQuery && (
                <>
                  <span className="pdf-toolbar__hits" role="status" aria-live="polite">
                    {searchMsg
                      ? searchMsg
                      : `${activeMatch + 1}/${matches.length}${truncated ? "+" : ""}`}
                  </span>
                  <button
                    type="button"
                    onClick={() => goToMatch(activeMatch - 1)}
                    disabled={!matches.length}
                    aria-label="Vorheriger Treffer"
                  >
                    ‹
                  </button>
                  <button
                    type="button"
                    onClick={() => goToMatch(activeMatch + 1)}
                    disabled={!matches.length}
                    aria-label="Nächster Treffer"
                  >
                    ›
                  </button>
                </>
              )}
            </div>
          )}
        </div>

        <div className="pdf-canvas-wrap">
          <div className="pdf-page-layer">
            <Page
              key={page}
              pageNumber={page}
              scale={scale}
              renderTextLayer
              renderAnnotationLayer={false}
              loading={<p className="muted">Lade Seite …</p>}
              canvasBackground="#fff"
              className="pdf-page"
              onRenderSuccess={(p) =>
                setRendered({ w: p.width, h: p.height })
              }
            />
            {docId && rendered && overlayDims && overlayBoxes.length > 0 && (
              <OcrOverlay
                matches={overlayBoxes}
                activeIndex={overlayActiveIndex}
                layoutWidth={overlayDims.width}
                layoutHeight={overlayDims.height}
                renderedWidth={rendered.w}
                renderedHeight={rendered.h}
              />
            )}
          </div>
        </div>
      </div>
    </Document>
  );
}
