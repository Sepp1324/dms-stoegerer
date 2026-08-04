import { PdfViewer } from "./PdfViewer";

// Linke Spalte der Detailansicht: große Dokumentvorschau (STOAA-430). PDFs werden
// clientseitig via PDF.js/Canvas gerendert (PdfViewer) – KEIN iframe mehr, damit
// die iframe/Blob/CSP-Vorschau-Bugs (weiße Seite nach Deploy, Safari-frame-ancestors)
// endgültig entfallen. Raster-Bilder (image/*, kein SVG – so garantiert es das
// Backend via Magic-Byte-Allowlist) werden direkt als <img> gezeigt.
export function DetailPreview({
  pdfUrl,
  mime,
  pdfError,
  title,
  initialPage,
  docId,
  layoutVersion,
}: {
  pdfUrl: string | null;
  // Erkannter MIME-Typ des Vorschau-Blobs (application/pdf oder image/*).
  mime: string | null;
  pdfError: string | null;
  title: string;
  initialPage?: number | null;
  // Aktiviert das OCR-Overlay/die In-Dokument-Suche (nur in der Detailansicht).
  docId?: number;
  layoutVersion?: number | null;
}) {
  const isPdf = (mime || "").toLowerCase().startsWith("application/pdf");

  return (
    <section className="card detail-preview">
      {pdfError && <p className="status status--warn">Vorschau: {pdfError}</p>}
      {!pdfError && !pdfUrl && <p className="muted">Lade Vorschau …</p>}
      {pdfUrl &&
        !pdfError &&
        (isPdf ? (
          <PdfViewer
            url={pdfUrl}
            title={title}
            initialPage={initialPage}
            docId={docId}
            layoutVersion={layoutVersion}
          />
        ) : (
          <img className="preview-image" src={pdfUrl} alt={`Vorschau: ${title}`} />
        ))}
    </section>
  );
}
