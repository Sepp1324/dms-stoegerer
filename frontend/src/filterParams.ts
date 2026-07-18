// Listen-Filter <-> URL-Query (#7, Stage 2). Nur die Kern-Filter der
// Dokumente-Liste; leere Werte werden ausgelassen, damit die URL knapp bleibt.
export interface ListFilters {
  q: string;
  correspondent: number | "";
  documentType: number | "";
  tag: number | "";
  storagePath: number | "";
  folder: number | "none" | "";
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
  if (f.ordering) p.set("ordering", f.ordering);
  if (f.page > 1) p.set("page", String(f.page));
  return p;
}
