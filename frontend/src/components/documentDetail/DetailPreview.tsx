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
        <iframe className="pdf-frame" src={pdfUrl} title={`Vorschau: ${title}`} />
      )}
    </section>
  );
}
