// Gemeinsame Anzeige-Bausteine für den Dokumentverarbeitungsstatus (STOAA-249):
// der kompakte Listen-Badge und die deutschen Label-Maps, die sowohl die Liste
// (DocumentsPage) als auch das Detail-Widget (DocumentDetail) nutzen. Reine
// Präsentation – die Daten kommen ausschließlich aus der API-Schicht (api.ts).
import type { OcrStatus, ProcessingState } from "../api";

// Kompakter Badge: In-Flight-States (uploaded…sealed) teilen sich Label und
// CSS-Modifier „processing"; failed/retry_pending/ready sind eigene Buckets.
const BADGE_META: Record<ProcessingState, { label: string; modifier: string }> = {
  uploaded: { label: "In Verarbeitung", modifier: "processing" },
  hashed: { label: "In Verarbeitung", modifier: "processing" },
  ocr_running: { label: "In Verarbeitung", modifier: "processing" },
  ocr_done: { label: "In Verarbeitung", modifier: "processing" },
  classification_running: { label: "In Verarbeitung", modifier: "processing" },
  classified: { label: "In Verarbeitung", modifier: "processing" },
  thumbnail_done: { label: "In Verarbeitung", modifier: "processing" },
  sealed: { label: "In Verarbeitung", modifier: "processing" },
  ready: { label: "Bereit", modifier: "ready" },
  failed: { label: "Fehlgeschlagen", modifier: "failed" },
  retry_pending: { label: "Wartet auf Retry", modifier: "retry_pending" },
};

// Deutsche Volltext-Labels je State (für das Detail-Widget, nicht den Bucket).
const STATE_LABELS: Record<ProcessingState, string> = {
  uploaded: "Hochgeladen",
  hashed: "Gehasht",
  ocr_running: "OCR läuft",
  ocr_done: "OCR abgeschlossen",
  classification_running: "Klassifizierung läuft",
  classified: "Klassifiziert",
  thumbnail_done: "Vorschau erzeugt",
  sealed: "Versiegelt",
  ready: "Bereit",
  failed: "Fehlgeschlagen",
  retry_pending: "Wartet auf Retry",
};

const OCR_STATUS_LABELS: Record<OcrStatus, string> = {
  pending: "Ausstehend",
  running: "Läuft",
  success: "Erfolgreich",
  failed: "Fehlgeschlagen",
  skipped: "Übersprungen",
};

// Kompakter Chip für die Dokumentliste. Fehlt der State (Altdaten ohne
// current_version), rendert der Badge sachlich „Unbekannt".
export function ProcessingBadge({ state }: { state: ProcessingState | null }) {
  const meta = state ? BADGE_META[state] : undefined;
  const label = meta?.label ?? "Unbekannt";
  const modifier = meta?.modifier ?? "unknown";
  return (
    <span className={`processing-badge processing-badge--${modifier}`}>{label}</span>
  );
}

export function processingStateLabel(state: ProcessingState | null): string {
  return state ? STATE_LABELS[state] ?? state : "Unbekannt";
}

export function ocrStatusLabel(status: OcrStatus | null): string {
  return status ? OCR_STATUS_LABELS[status] ?? status : "—";
}
