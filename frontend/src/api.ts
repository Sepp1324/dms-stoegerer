// Zentrale API-Schicht: JWT-Auth (mit Refresh) + Endpunkte.
// Tokens liegen im localStorage; bei 401 wird einmal automatisch refreshed.

const API_BASE = "/api";
const ACCESS_KEY = "dms_access";
const REFRESH_KEY = "dms_refresh";

export class AuthError extends Error {}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY);
}
export function isLoggedIn(): boolean {
  return !!getAccessToken();
}
function setTokens(access: string, refresh?: string) {
  localStorage.setItem(ACCESS_KEY, access);
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
}
export function logout() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export async function login(username: string, password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/token/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    throw new AuthError("Anmeldung fehlgeschlagen – Benutzername oder Passwort falsch.");
  }
  const data = await res.json();
  setTokens(data.access, data.refresh);
}

async function tryRefresh(): Promise<boolean> {
  const refresh = localStorage.getItem(REFRESH_KEY);
  if (!refresh) return false;
  const res = await fetch(`${API_BASE}/auth/token/refresh/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
  });
  if (!res.ok) return false;
  const data = await res.json();
  setTokens(data.access);
  return true;
}

// fetch-Wrapper: hängt den Access-Token an und erneuert ihn bei 401 einmalig.
async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const withAuth = (): RequestInit => ({
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${getAccessToken()}`,
    },
  });

  let res = await fetch(`${API_BASE}${path}`, withAuth());
  if (res.status === 401 && (await tryRefresh())) {
    res = await fetch(`${API_BASE}${path}`, withAuth());
  }
  if (res.status === 401) {
    logout();
    throw new AuthError("Sitzung abgelaufen – bitte erneut anmelden.");
  }
  return res;
}

// --- Typen ---
export interface DocumentVersion {
  id: number;
  version_no: number;
  sha256: string;
  prev_hash: string;
  mime_type: string;
  size: number;
  page_count: number | null;
  is_immutable: boolean;
  created_by: number | null;
  created_by_name: string | null;
  has_archive: boolean;
  created_at: string;
}

// Ergebnis der Integritätsprüfung einer einzelnen Version (Hash-Kette).
export interface VersionIntegrity {
  version_no: number;
  sha256: string;
  computed_sha256: string;
  prev_hash: string;
  expected_prev_hash: string;
  file_present: boolean;
  file_ok: boolean;
  prev_ok: boolean;
}
export interface DocumentIntegrity {
  chain_ok: boolean;
  versions: VersionIntegrity[];
}
export interface DocumentItem {
  id: number;
  title: string;
  created_at: string | null;
  added_at: string;
  correspondent: number | null;
  correspondent_name: string | null;
  document_type: number | null;
  document_type_name: string | null;
  tags: { id: number; name: string; color: string }[];
  page_count: number | null;
}
export interface AiSuggestions {
  title?: string;
  document_type?: string;
  correspondent?: string;
  tags?: string[];
  summary?: string;
  // Belegdatum als ISO-String (YYYY-MM-DD); beim Übernehmen auf created_at gemappt.
  date?: string;
}
// Freigabe-Status (Stufe 4, STOAA-57/63). Bestandsdaten ohne Feld gelten als
// "entwurf"; das Backend liefert das Feld ab STOAA-63 verbindlich mit.
export type DocumentStatus =
  | "entwurf"
  | "zur_freigabe"
  | "freigegeben"
  | "abgelehnt";
export interface DocumentDetail extends DocumentItem {
  storage_path: number | null;
  storage_path_name: string | null;
  owner: number | null;
  current_version: number | null;
  status: DocumentStatus;
  ai_suggestions: AiSuggestions;
  ai_suggested_at: string | null;
  classification: Classification;
  versions: DocumentVersion[];
}
export interface Classification {
  rules?: string[];
  applied?: {
    document_type?: string;
    correspondent?: string;
    storage_path?: string;
    tags?: string[];
  };
}
export interface ClassificationRule {
  id: number;
  name: string;
  priority: number;
  enabled: boolean;
  match: { text_contains?: string[]; text_regex?: string };
  then: {
    document_type?: string;
    correspondent?: string;
    storage_path?: string;
    tags?: string[];
  };
}
export interface AuditEntry {
  id: number;
  timestamp: string;
  actor: number | null;
  actor_name: string;
  action: string;
  object_type: string;
  object_id: string;
  // Aktionsabhängige Nutzlast, z. B. { changes: { title: { from, to } } }.
  detail: Record<string, unknown>;
}
export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}
export interface NamedRef {
  id: number;
  name: string;
}
// Tags tragen zusätzlich eine Hex-Farbe (für den Farbpunkt in Karte & Sidebar).
export interface TagRef extends NamedRef {
  color: string;
}
export interface Me {
  id: number;
  username: string;
  email: string;
  role: string;
  is_dms_admin: boolean;
  can_write: boolean;
}

// --- Endpunkte ---
export interface DocumentQuery {
  q?: string;
  correspondent?: number | "";
  document_type?: number | "";
  tag?: number | "";
  // Speicherpfad-Filter (STOAA-50). Backend-Query-Param ``storage_path`` kommt
  // aus dem Kind-Ticket; unbekannte Params werden vom Backend ignoriert, daher
  // hier bereits vorbereitet.
  storage_path?: number | "";
  page?: number;
  // Sortierung, z. B. "-added_at" (Datum neu→alt), "added_at" (alt→neu),
  // "title" (A–Z). Leer = Backend-Standard (FTS-Relevanz bei ``q``, sonst
  // ``-added_at``). Wird von getDocuments nur gesetzt, wenn nicht leer.
  ordering?: string;
}

export async function getDocuments(
  query: DocumentQuery = {},
): Promise<Paginated<DocumentItem>> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "" && value !== null) {
      params.set(key, String(value));
    }
  }
  const res = await apiFetch(`/documents/?${params.toString()}`);
  if (!res.ok) throw new Error(`Laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function getDocument(id: number): Promise<DocumentDetail> {
  const res = await apiFetch(`/documents/${id}/`);
  if (!res.ok) throw new Error(`Dokument laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

// Verlauf/Audit-Trail eines Dokuments (paginiert, neueste zuerst).
export async function getDocumentAudit(
  id: number,
  page = 1,
): Promise<Paginated<AuditEntry>> {
  const res = await apiFetch(`/documents/${id}/audit/?page=${page}`);
  if (!res.ok) throw new Error(`Verlauf laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

// Lädt das Vorschau-PDF als Blob (mit Auth-Header) – ein <iframe src="…/preview/">
// würde den Bearer-Token nicht mitschicken, daher der Umweg über fetch + Blob-URL.
// Ohne ``versionNo`` die aktuelle Version, sonst gezielt eine ältere.
export async function getDocumentPreview(
  id: number,
  versionNo?: number,
): Promise<Blob> {
  const suffix = versionNo ? `?version=${versionNo}` : "";
  const res = await apiFetch(`/documents/${id}/preview/${suffix}`);
  if (!res.ok) throw new Error(`Vorschau nicht verfügbar (HTTP ${res.status})`);
  return res.blob();
}

// Integritätsprüfung der Hash-Kette eines Dokuments (rechnet Datei-Hashes nach).
export async function getDocumentIntegrity(id: number): Promise<DocumentIntegrity> {
  const res = await apiFetch(`/documents/${id}/integrity/`);
  if (!res.ok) throw new Error(`Integritätsprüfung fehlgeschlagen (HTTP ${res.status})`);
  return res.json();
}

// Lädt die Originaldatei einer Version als Blob (mit Auth-Header) zum Download.
export async function getDocumentVersionFile(
  id: number,
  versionNo: number,
): Promise<Blob> {
  const res = await apiFetch(`/documents/${id}/download/?version=${versionNo}`);
  if (!res.ok) throw new Error(`Download fehlgeschlagen (HTTP ${res.status})`);
  return res.blob();
}

// Hängt eine neue Datei als nächste Version an ein bestehendes Dokument.
export async function addDocumentVersion(
  id: number,
  file: File,
): Promise<DocumentDetail> {
  const form = new FormData();
  form.append("file", file);
  const res = await apiFetch(`/documents/${id}/add_version/`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      /* keine JSON-Fehlermeldung */
    }
    throw new Error(detail);
  }
  return res.json();
}

// Miniaturbild der ersten Seite (JPEG) – ebenfalls per fetch+Blob wegen JWT.
export async function getDocumentThumbnail(id: number): Promise<Blob> {
  const res = await apiFetch(`/documents/${id}/thumbnail/`);
  if (!res.ok) throw new Error(`Kein Thumbnail (HTTP ${res.status})`);
  return res.blob();
}

export interface DocumentPatch {
  title?: string;
  correspondent?: number | null;
  document_type?: number | null;
  storage_path?: number | null;
  tag_ids?: number[];
}

export async function updateDocument(
  id: number,
  patch: DocumentPatch,
): Promise<DocumentDetail> {
  const res = await apiFetch(`/documents/${id}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* keine JSON-Fehlermeldung */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function applySuggestions(
  id: number,
  fields?: string[],
): Promise<DocumentDetail> {
  const res = await apiFetch(`/documents/${id}/apply_suggestions/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields ? { fields } : {}),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* keine JSON-Fehlermeldung */
    }
    throw new Error(detail);
  }
  return res.json();
}

// Regeneriert die KI-Vorschläge synchron. Bei fehlendem Provider liefert das
// Backend Status 200 mit source:"unavailable" (nichts wird überschrieben).
export interface SuggestResult extends DocumentDetail {
  source: "ai" | "unavailable" | string;
}

export async function suggestDocument(id: number): Promise<SuggestResult> {
  const res = await apiFetch(`/documents/${id}/suggest/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* keine JSON-Fehlermeldung */
    }
    throw new Error(detail);
  }
  return res.json();
}

// Verwirft einzelne KI-Vorschlagsfelder, ohne sie anzuwenden.
export async function dismissSuggestions(
  id: number,
  fields: string[],
): Promise<DocumentDetail> {
  const res = await apiFetch(`/documents/${id}/dismiss_suggestions/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* keine JSON-Fehlermeldung */
    }
    throw new Error(detail);
  }
  return res.json();
}

// --- Freigabe-Workflow (Stufe 4, Kontrakt aus STOAA-63) ---
// Jede Aktion liefert das aktualisierte Dokument (inkl. neuem status) zurück.
// Ungültiger Übergang → 4xx, Gast → 403; Fehlermeldung analog zu updateDocument.
async function postDocAction(
  id: number,
  action: "submit" | "approve" | "reject",
  body: unknown = {},
): Promise<DocumentDetail> {
  const res = await apiFetch(`/documents/${id}/${action}/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* keine JSON-Fehlermeldung */
    }
    throw new Error(detail);
  }
  return res.json();
}

// Dokument zur Freigabe einreichen (nur aus entwurf|abgelehnt).
export function submitDocument(id: number): Promise<DocumentDetail> {
  return postDocAction(id, "submit");
}
// Dokument genehmigen (nur aus zur_freigabe).
export function approveDocument(id: number): Promise<DocumentDetail> {
  return postDocAction(id, "approve");
}
// Dokument ablehnen (nur aus zur_freigabe), optional mit Begründung.
export function rejectDocument(
  id: number,
  reason?: string,
): Promise<DocumentDetail> {
  return postDocAction(id, "reject", reason ? { reason } : {});
}

export async function getMe(): Promise<Me> {
  const res = await apiFetch("/me/");
  if (!res.ok) throw new Error(`Profil laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function uploadDocument(file: File, title?: string): Promise<DocumentItem> {
  const form = new FormData();
  form.append("file", file);
  if (title) form.append("title", title);
  // Kein Content-Type setzen – der Browser ergänzt die multipart-Boundary selbst.
  const res = await apiFetch("/documents/upload/", { method: "POST", body: form });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      /* keine JSON-Fehlermeldung */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function getCorrespondents(): Promise<NamedRef[]> {
  return listAll<NamedRef>("/correspondents/");
}
export async function getDocumentTypes(): Promise<NamedRef[]> {
  return listAll<NamedRef>("/document-types/");
}
export async function getTags(): Promise<TagRef[]> {
  return listAll<TagRef>("/tags/");
}
export async function getStoragePaths(): Promise<NamedRef[]> {
  return listAll<NamedRef>("/storage-paths/");
}

// --- Klassifizierungsregeln ---
export async function getRules(): Promise<ClassificationRule[]> {
  return listAll<ClassificationRule>("/classification-rules/");
}
export function createRule(
  rule: Omit<ClassificationRule, "id">,
): Promise<ClassificationRule> {
  return postJson<ClassificationRule>("/classification-rules/", rule);
}
export async function deleteRule(id: number): Promise<void> {
  const res = await apiFetch(`/classification-rules/${id}/`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) throw new Error(`Löschen fehlgeschlagen: HTTP ${res.status}`);
}

// --- Stammdaten inline anlegen ---
async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* keine JSON-Fehlermeldung */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const createCorrespondent = (name: string) =>
  postJson<NamedRef>("/correspondents/", { name });
export const createDocumentType = (name: string) =>
  postJson<NamedRef>("/document-types/", { name });
export const createTag = (name: string) =>
  postJson<TagRef>("/tags/", { name });
export const createStoragePath = (name: string) =>
  postJson<NamedRef>("/storage-paths/", {
    name,
    path_template: "archive/{jahr}/{korrespondent}/{titel}",
  });

// Hilfsfunktion: paginierte Liste in ein flaches Array einsammeln (erste Seite genügt hier).
async function listAll<T>(path: string): Promise<T[]> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(`Laden fehlgeschlagen: HTTP ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : data.results;
}
