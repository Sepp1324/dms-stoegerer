import type { ProcessingStateFilter } from "./api";

// Listen-Filter <-> URL-Query (#7, Stage 2/2b). Nur die Filter der Dokumente-
// Liste; leere Werte werden ausgelassen, damit die URL knapp bleibt.
export type SharedScope = "with-me" | "by-me";

export const PROCESSING_STATES: readonly ProcessingStateFilter[] = [
  "failed",
  "processing",
  "ready",
  "retry_pending",
];
export const SHARED_SCOPES: readonly SharedScope[] = ["with-me", "by-me"];

export interface ListFilters {
  q: string;
  correspondent: number | "";
  documentType: number | "";
  tag: number | "";
  storagePath: number | "";
  folder: number | "none" | "";
  processingState: ProcessingStateFilter | "";
  sharedScope: SharedScope | "";
  ordering: string;
  page: number;
}

export function buildFilterParams(f: ListFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.correspondent !== "") p.set("correspondent", String(f.correspondent));
  if (f.documentType !== "") p.set("document_type", String(f.documentType));
  if (f.tag !== "") p.set("tag", String(f.tag));
  if (f.storagePath !== "") p.set("storage_path", String(f.storagePath));
  if (f.folder !== "") p.set("folder", String(f.folder));
  if (f.processingState) p.set("processing_state", f.processingState);
  if (f.sharedScope) p.set("shared", f.sharedScope);
  if (f.ordering) p.set("ordering", f.ordering);
  if (f.page > 1) p.set("page", String(f.page));
  return p;
}

// Parse-Helfer für die Lazy-Init aus der URL: nur zulässige Werte werden
// übernommen (Garbage in der Query -> Filter leer statt fehlerhaft).
export function parseProcessingState(v: string | null): ProcessingStateFilter | "" {
  return v && (PROCESSING_STATES as string[]).includes(v)
    ? (v as ProcessingStateFilter)
    : "";
}

export function parseSharedScope(v: string | null): SharedScope | "" {
  return v && (SHARED_SCOPES as string[]).includes(v) ? (v as SharedScope) : "";
}
