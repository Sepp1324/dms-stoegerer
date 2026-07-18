// Linke Spalte der Detailansicht: große PDF-/Bild-Vorschau (STOAA-430). Wird beim
// Scrollen der rechten Spalte per CSS sticky gehalten. Aus DocumentDetail.tsx
// extrahiert (STOAA-431) – Verhalten unverändert.
export function DetailPreview({
  pdfUrl,
  pdfError,
  title,
}: {
  pdfUrl: string | null;
  pdfError: string | null;
  title: string;
}) {
  return (
    <section className="card detail-preview">
      {pdfError && <p className="status status--warn">Vorschau: {pdfError}</p>}
      {!pdfError && !pdfUrl && <p className="muted">Lade Vorschau …</p>}
      {pdfUrl && (
        // Sicherheit (P0-2): `sandbox` OHNE allow-scripts/allow-same-origin
        // erzwingt einen opaken Origin ohne Skriptausführung – eine (theoretisch
        // durchgerutschte) HTML/SVG-Datei kann so nicht auf localStorage/Cookies
        // des DMS zugreifen. PDFs rendern der native Viewer bzw. pdf.js trotzdem;
        // allow-downloads erhält den Speichern-Button.
        <iframe
          className="pdf-frame"
          src={pdfUrl}
          title={`Vorschau: ${title}`}
          sandbox="allow-downloads"
        />
      )}
    </section>
  );
}
