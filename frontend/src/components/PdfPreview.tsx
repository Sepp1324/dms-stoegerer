import { useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

// Styles für die Textauswahl (OCR) und Annotationen
import "react-pdf/dist/esm/Page/AnnotationLayer.css";
import "react-pdf/dist/esm/Page/TextLayer.css";

// Worker konfigurieren (notwendig für Vite, damit das PDF im Hintergrund gerendert wird)
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).toString();

export default function PdfPreview({ url }: { url: string }) {
  const [numPages, setNumPages] = useState<number>();
  const [pageNumber, setPageNumber] = useState<number>(1);
  const [scale, setScale] = useState<number>(1.0);

  function onDocumentLoadSuccess({ numPages }: { numPages: number }) {
    setNumPages(numPages);
    setPageNumber(1);
  }

  return (
    <div className="pdf-preview" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="pdf-controls" style={{ display: "flex", gap: "1rem", padding: "0.5rem", background: "var(--bg-card)", borderBottom: "1px solid var(--border)" }}>
        
        {/* Paginierung */}
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button 
            disabled={pageNumber <= 1} 
            onClick={() => setPageNumber(p => p - 1)}
          >
            ←
          </button>
          <span className="muted">
            Seite {pageNumber} von {numPages || "—"}
          </span>
          <button 
            disabled={pageNumber >= (numPages || 1)} 
            onClick={() => setPageNumber(p => p + 1)}
          >
            →
          </button>
        </div>

        <div style={{ flex: 1 }} />

        {/* Zoom */}
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button 
            disabled={scale <= 0.5} 
            onClick={() => setScale(s => s - 0.2)}
          >
            -
          </button>
          <span className="muted">{Math.round(scale * 100)}%</span>
          <button 
            disabled={scale >= 3.0} 
            onClick={() => setScale(s => s + 0.2)}
          >
            +
          </button>
        </div>
      </div>

      <div className="pdf-document" style={{ flex: 1, overflow: "auto", padding: "1rem", display: "flex", justifyContent: "center", background: "#f3f4f6" }}>
        <Document
          file={url}
          onLoadSuccess={onDocumentLoadSuccess}
          loading={<p className="muted">Lade PDF...</p>}
          error={<p className="status status--error">Fehler beim Laden des PDFs.</p>}
        >
          <Page 
            pageNumber={pageNumber} 
            scale={scale} 
            renderTextLayer={true} 
            renderAnnotationLayer={true} 
            className="pdf-page-shadow"
          />
        </Document>
      </div>
    </div>
  );
}