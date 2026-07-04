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
  // Zusatzfelder-Werte dieses Dokuments (STOAA-108/112). Nur gesetzte Werte sind
  // enthalten; die vollständige Feldliste kommt aus getCustomFields().
  custom_field_values: CustomFieldValue[];
}

// --- Zusatzfelder (Custom Fields, STOAA-108/113) ---
// Kanonisches Storage-Format (Backend-Kontrakt STOAA-112): NUMBER/CURRENCY mit
// Punkt-Dezimal ("1234.56"), DATE ISO ("YYYY-MM-DD"), BOOLEAN "true"/"false",
// TEXT roh. Das Frontend konvertiert für Anzeige/Eingabe ins deutsche Format.
export type CustomFieldDataType =
  | "text"
  | "number"
  | "date"
  | "currency"
  | "boolean";

// Definition eines Zusatzfeldes (global, admin-gepflegt).
export interface CustomField {
  id: number;
  name: string;
  data_type: CustomFieldDataType;
}

// Wert eines Zusatzfeldes an einem Dokument (nested im DocumentDetail).
// ``field`` ist die CustomField-PK; ``field_name``/``data_type`` liefert das
// Backend read-only mit, damit das Frontend ohne Extra-Lookup formatieren kann.
export interface CustomFieldValue {
  field: number;
  field_name: string;
  data_type: CustomFieldDataType;
  value: string;
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
  // Zusatzfeld-Filter (STOAA-108/113): Query-Param-Name → Wert, z. B.
  // { "custom_field_3_gte": "100.00", "custom_field_3_lte": "500.00" }.
  // Werte sind bereits kanonisch (Punkt-Dezimal); leere Einträge werden
  // ausgelassen. Backend ignoriert unbekannte/ungültige Grenzen (kein 500).
  customFilters?: Record<string, string>;
}

export async function getDocuments(
  query: DocumentQuery = {},
): Promise<Paginated<DocumentItem>> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (key === "customFilters") continue; // gesondert behandelt (dynamische Keys)
    if (value !== undefined && value !== "" && value !== null) {
      params.set(key, String(value));
    }
  }
  for (const [key, value] of Object.entries(query.customFilters ?? {})) {
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
  // Zusatzfeld-Werte als Upsert-Liste (STOAA-112): jeder Eintrag mit
  // CustomField-PK und kanonischem Wert. Leerer Wert = Feld am Dokument löschen.
  // Fehlt der Key ganz, bleiben vorhandene Werte unangetastet.
  custom_field_values?: { field: number; value: string }[];
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

// --- Zusatzfelder (Custom Fields) ---
// CRUD unter /api/custom-fields/ (STOAA-112). DELETE liefert 409, wenn noch
// Werte am Feld hängen (kein Datenverlust); die UI fängt das ab.
export async function getCustomFields(): Promise<CustomField[]> {
  return listAll<CustomField>("/custom-fields/");
}
export function createCustomField(
  name: string,
  data_type: CustomFieldDataType,
): Promise<CustomField> {
  return postJson<CustomField>("/custom-fields/", { name, data_type });
}
// Nur der Name ist änderbar – data_type ist serverseitig read-only (Typwechsel
// wäre breaking, siehe Spec §3.1).
export async function updateCustomField(
  id: number,
  name: string,
): Promise<CustomField> {
  const res = await apiFetch(`/custom-fields/${id}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
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
export async function deleteCustomField(id: number): Promise<void> {
  const res = await apiFetch(`/custom-fields/${id}/`, { method: "DELETE" });
  if (res.ok || res.status === 204) return;
  let detail = `HTTP ${res.status}`;
  try {
    const data = await res.json();
    detail = data.detail || JSON.stringify(data);
  } catch {
    /* keine JSON-Fehlermeldung */
  }
  // 409 = Feld ist noch in Dokumenten verwendet (Backend-Schutz).
  throw new Error(detail);
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

// --- Freigabe-Aufruf (Share-Access, STOAA-193) ---
// Abruf eines geteilten Dokuments über den Klartext-Token (Aufruf-Seite
// /share/<token>). Login-PFLICHT: apiFetch hängt den JWT an und erneuert ihn bei
// 401 einmalig; schlägt auch das fehl, wirft es ``AuthError`` (→ zur Anmeldung).
// Ein unbekannter/widerrufener/abgelaufener Token liefert **410 Gone** → wir
// werfen ``ShareGoneError``, damit die UI die klare Seite „Link nicht mehr
// gültig" zeigt (kein Roh-404/500). Die Endpunkte tragen bewusst KEINEN
// Trailing-Slash (Backend-Kontrakt STOAA-191), sonst verwürfe ein
// APPEND_SLASH-Redirect den Authorization-Header.
export class ShareGoneError extends Error {}

// Vorschau-PDF des geteilten Dokuments als Blob (inline, für <iframe>).
export async function getSharePreview(token: string): Promise<Blob> {
  const res = await apiFetch(`/share/${encodeURIComponent(token)}/preview`);
  if (res.status === 410) throw new ShareGoneError();
  if (!res.ok) throw new Error(`Vorschau nicht verfügbar (HTTP ${res.status})`);
  return res.blob();
}

// Ergebnis des Share-Downloads: die Original-Bytes plus der vom Backend
// vorgeschlagene Dateiname (aus Content-Disposition, sonst generischer Fallback).
export interface ShareDownload {
  blob: Blob;
  filename: string;
}

// Original-Datei des geteilten Dokuments herunterladen (Blob + Dateiname).
export async function getShareDownload(token: string): Promise<ShareDownload> {
  const res = await apiFetch(`/share/${encodeURIComponent(token)}/download`);
  if (res.status === 410) throw new ShareGoneError();
  if (!res.ok) throw new Error(`Download fehlgeschlagen (HTTP ${res.status})`);
  const blob = await res.blob();
  return {
    blob,
    filename: parseContentDispositionFilename(res.headers.get("Content-Disposition")),
  };
}

// Liest den Dateinamen aus einem Content-Disposition-Header. Bevorzugt das
// RFC-5987-``filename*`` (UTF-8, Umlaute), fällt auf schlichtes ``filename="…"``
// und zuletzt auf einen generischen Namen zurück.
function parseContentDispositionFilename(disposition: string | null): string {
  const fallback = "freigegebenes-dokument";
  if (!disposition) return fallback;
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(disposition);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^["']|["']$/g, ""));
    } catch {
      /* ungültige Kodierung – auf plain filename zurückfallen */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1].trim() : fallback;
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
