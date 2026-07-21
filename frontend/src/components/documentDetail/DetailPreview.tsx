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
        // Chromes nativer PDF-Viewer braucht Scripting UND same-origin – sonst
        // rendert das iframe eine LEERE Seite (verifiziert: `allow-downloads`
        // allein blieb leer, ebenso `allow-scripts allow-downloads`; erst mit
        // allow-same-origin erscheint das PDF). Bilder bräuchten kein Scripting,
        // PDFs schon – deshalb war die Vorschau für PDF-Dokumente komplett leer.
        //
        // Die XSS-Härtung (P0-2) ruht damit auf der Ebene DAVOR, nicht auf dem
        // iframe-sandbox: Die Magic-Byte-Allowlist beim Ingest lässt NUR echte
        // PDFs/Bilder in den Bestand, und die Auslieferung setzt
        // `Content-Type: application/pdf` + `X-Content-Type-Options: nosniff`.
        // Eine getarnte HTML/SVG-Datei wird so gar nicht erst gespeichert bzw.
        // nie als HTML interpretiert; PDF-eigenes JavaScript führt Chrome ohnehin
        // nicht aus. `allow-downloads` erhält den Speichern-Button.
        // (Spätere Härtung möglich: Vorschau aus separatem Origin ausliefern oder
        // clientseitig via pdf.js zu Canvas rendern statt nativem Viewer.)
        <iframe
          className="pdf-frame"
          src={pdfUrl}
          title={`Vorschau: ${title}`}
          sandbox="allow-scripts allow-same-origin allow-downloads"
        />
      )}
    </section>
  );
}
