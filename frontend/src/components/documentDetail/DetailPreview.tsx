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
        // BEWUSST KEIN `sandbox`: Chromes nativer PDF-Viewer rendert nur in einem
        // komplett un-sandboxed iframe. Verifiziert im Browser – JEDE Sandbox-
        // Variante (`allow-downloads`, `allow-scripts allow-downloads`, sogar
        // `allow-scripts allow-same-origin allow-downloads`) ließ die PDF-Vorschau
        // LEER; erst ohne das Attribut erscheint das Dokument.
        //
        // Sicherheit: Die XSS-Abwehr (P0-2) sitzt eine Ebene davor, nicht am
        // iframe. Das Vorschau-iframe kann NUR echte PDFs/Raster-Bilder laden,
        // weil (1) die Magic-Byte-Allowlist beim Ingest HTML/SVG/XML gar nicht
        // erst speichert, (2) `is_safe_inline` alles außer PDF/Raster-Bild als
        // Download erzwingt und SVG explizit ausschließt, und (3) die Auslieferung
        // `Content-Type: application/pdf|image/*` + `X-Content-Type-Options:
        // nosniff` setzt. Aktiver Inhalt (Script/HTML/SVG) erreicht dieses iframe
        // also nicht; PDF-eigenes JS führt Chrome ohnehin nicht aus.
        // (Spätere Härtung möglich: Vorschau aus separatem Origin ausliefern oder
        // clientseitig via pdf.js zu Canvas rendern statt nativem Viewer.)
        <iframe
          className="pdf-frame"
          src={pdfUrl}
          title={`Vorschau: ${title}`}
        />
      )}
    </section>
  );
}
