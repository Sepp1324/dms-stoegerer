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
  created_at: string;
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
  page?: number;
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
export async function getTags(): Promise<NamedRef[]> {
  return listAll<NamedRef>("/tags/");
}

// Hilfsfunktion: paginierte Liste in ein flaches Array einsammeln (erste Seite genügt hier).
async function listAll<T>(path: string): Promise<T[]> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(`Laden fehlgeschlagen: HTTP ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : data.results;
}
