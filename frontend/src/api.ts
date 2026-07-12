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
// Fachliche State-Machine der asynchronen Dokumentverarbeitung (STOAA-248).
// ``ocr_status`` bleibt das technische Detail-Monitoring des OCR-Schritts;
// ``processing_state`` beschreibt den gesamten DMS-Fluss (uploaded → … → ready)
// samt Fehler-/Retry-States. Serverseitig gesetzt, read-only.
export type ProcessingState =
  | "uploaded"
  | "hashed"
  | "ocr_running"
  | "ocr_done"
  | "classification_running"
  | "classified"
  | "thumbnail_done"
  | "sealed"
  | "ready"
  | "failed"
  | "retry_pending";

// Technischer OCR-Detailstatus des aktuellen Verarbeitungsschritts (STOAA-225).
export type OcrStatus = "pending" | "running" | "success" | "failed" | "skipped";

// Fachlicher Review-Status: getrennt vom technischen processing_state.
// ``needs_review`` landet in der Inbox; ``reviewed`` wurde menschlich bestätigt.
export type ReviewStatus = "needs_review" | "reviewed";
export type ArchiveStatus = "unchecked" | "ok" | "warning" | "error";
export type RetentionState = "none" | "active" | "due_soon" | "expired" | "legal_hold";
export type ReviewTaskStatus = "open" | "resolved" | "ignored";
export type ReviewTaskKind =
  | "metadata_missing"
  | "ocr_failed"
  | "ocr_empty"
  | "classification_low_confidence"
  | "ai_suggestion_pending"
  | "extraction_pending"
  | "case_file_pending"
  | "duplicate_suspected"
  | "asn_missing"
  | "email_needs_review"
  | "contract_review";

// UI-Buckets für den Listen-Filter ``?processing_state=`` (STOAA-248). ``processing``
// fasst alle In-Flight-States (uploaded…sealed) zusammen; failed/retry_pending/ready
// sind eigene Buckets. Unbekannte Werte ignoriert das Backend (kein Filter).
export type ProcessingStateFilter =
  | "failed"
  | "processing"
  | "ready"
  | "retry_pending";

export interface DocumentVersion {
  id: number;
  version_no: number;
  sha256: string;
  prev_hash: string;
  // Verarbeitungs-/Fehler-/Retry-Felder (STOAA-228/248) und OCR-Detailstatus
  // (STOAA-225). Alle serverseitig (Pipeline) gesetzt und read-only.
  processing_state: ProcessingState;
  processing_error: string;
  processing_failed_step: string;
  processing_failed_at: string | null;
  processing_attempts: number;
  ocr_status: OcrStatus;
  ocr_error: string;
  mime_type: string;
  size: number;
  page_count: number | null;
  is_immutable: boolean;
  retention_until: string | null;
  snapshot_schema_version: number;
  snapshot_taken_at: string | null;
  seal_hash: string;
  seal_ok: boolean;
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
export interface DocumentArchiveReport {
  status: ArchiveStatus;
  checked_at: string;
  integrity: DocumentIntegrity;
  warnings: string[];
  errors: string[];
}
export interface PdfWorkbenchPage {
  page: number;
  rotation: number;
}
export interface PdfWorkbenchManifest {
  document: number;
  version_id: number;
  version_no: number;
  page_count: number;
  pages: PdfWorkbenchPage[];
}
export interface PdfWorkbenchPageSpec {
  page: number;
  rotation?: 0 | 90 | 180 | 270;
}
export interface PdfWorkbenchSplitPart {
  title: string;
  pages: number[];
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
  folder: number | null;
  folder_name: string | null;
  folder_path: string | null;
  case_file: number | null;
  case_file_title: string | null;
  tags: { id: number; name: string; color: string }[];
  page_count: number | null;
  // Verarbeitungs-Rollup der aktuellen Version (STOAA-248): spart der Liste den
  // Durchgriff auf ``versions``. Altdaten ohne current_version liefern ``null``.
  processing_state: ProcessingState | null;
  review_status: ReviewStatus;
  retention_until: string | null;
  retention_state: {
    state: RetentionState;
    retention_until: string | null;
    days_remaining: number | null;
  };
  legal_hold: boolean;
  legal_hold_reason: string;
  legal_hold_set_at: string | null;
  archive_status: ArchiveStatus;
  archive_status_label: string;
  archive_checked_at: string | null;
  archive_error: string;
  review_task_count: number;
  review_tasks: ReviewTask[];
  ocr_status: OcrStatus | null;
  // Suchergebnis-Snippet (STOAA-368/370): sicheres HTML mit ``<mark>`` rund um den
  // Treffer. Nur bei aktiver Volltextsuche (``?q=``) gefüllt; sonst / kein Treffer
  // im OCR-Text ``null``. Vor dem Rendern via ``sanitizeSnippet`` säubern.
  snippet: string | null;
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

export type ExtractionCandidateField =
  | "document_date"
  | "amount"
  | "iban"
  | "contract_number"
  | "policy_number";
export type ExtractionCandidateStatus = "pending" | "applied" | "dismissed";
export interface ExtractionCandidate {
  id: number;
  document: number;
  field: ExtractionCandidateField;
  field_label: string;
  value: string;
  normalized_value: string;
  confidence: number;
  reason: string;
  source: string;
  source_page: number | null;
  source_snippet: string;
  source_snippet_html: string;
  status: ExtractionCandidateStatus;
  created_at: string;
  applied_at: string | null;
  dismissed_at: string | null;
}
export interface ReviewTask {
  id: number;
  document: number;
  document_title: string;
  kind: ReviewTaskKind;
  kind_label: string;
  status: ReviewTaskStatus;
  status_label: string;
  priority: number;
  message: string;
  suggested_action: string;
  data: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  resolved_by: number | null;
  asn_label: string | null;
}
export interface InboxSummary {
  total_needs_review: number;
  ready: number;
  processing: number;
  failed: number;
  retry_pending: number;
  with_ai_suggestions: number;
  pending_extraction_candidates: number;
  pending_case_candidates: number;
  open_review_tasks: number;
  review_task_kinds: Partial<Record<ReviewTaskKind, number>>;
  oldest_added_at: string | null;
}
export interface InboxGenerateCandidatesResult {
  documents: number;
  extraction_created: number;
  case_created: number;
  errors: { id?: number; error: string }[];
}
export type AutopilotLane =
  | "ready"
  | "suggestions"
  | "metadata"
  | "processing"
  | "error";
export interface AutopilotSuggestion {
  kind: "ai" | "extraction" | "case_file";
  field: string;
  label: string;
  value: string;
  confidence: number;
  source: string;
  reason?: string;
  action: {
    type:
      | "apply_ai_suggestion"
      | "apply_extraction_candidate"
      | "apply_case_file_candidate";
    fields?: string[];
    candidate_id?: number;
  };
}
export interface AutopilotNextAction {
  kind: string;
  label: string;
  tone: "primary" | "warn" | "danger" | "neutral" | "ok";
}
export interface AutopilotItem {
  document: number;
  title: string;
  asn_label: string | null;
  lane: AutopilotLane;
  lane_label: string;
  confidence: number;
  can_autofile: boolean;
  bulk_safe: boolean;
  missing_metadata: string[];
  suggestions: AutopilotSuggestion[];
  reasons: string[];
  next_actions: AutopilotNextAction[];
  signals: {
    review_tasks: number;
    ai_suggestions: number;
    extraction_candidates: number;
    case_candidates: number;
    classification_rules: number;
  };
}
export interface AutopilotInbox {
  total: number;
  limit: number;
  summary: {
    lanes: Record<AutopilotLane, number>;
    average_confidence: number;
    auto_ready: number;
    needs_human: number;
    pending_suggestions: number;
  };
  items: AutopilotItem[];
}
export interface ReviewLearningOptions {
  create_rule?: boolean;
  match_text?: string;
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
  // Archivnummer (STOAA-284/285). ``asn`` ist die rohe fortlaufende Zahl,
  // ``asn_label`` die kanonische Anzeigeform ``ASN000123``. Beide read-only,
  // serverseitig vergeben; ``null`` bei Altdaten ohne vergebene ASN.
  asn: number | null;
  asn_label: string | null;
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
    folder?: string;
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
    folder?: string;
    tags?: string[];
  };
}
export type ClassificationRulePayload = Omit<ClassificationRule, "id">;
export interface ClassificationRuleSimulationChange {
  field: string;
  to?: string;
  current?: string;
  value?: string | string[];
  add?: string[];
}
export interface ClassificationRuleSimulationMatch {
  id: number;
  title: string;
  asn_label: string | null;
  correspondent_name: string | null;
  document_type_name: string | null;
  folder_path: string | null;
  would_change: ClassificationRuleSimulationChange[];
  already_ok: ClassificationRuleSimulationChange[];
  conflicts: ClassificationRuleSimulationChange[];
}
export interface ClassificationRuleSimulation {
  total_documents: number;
  matched: number;
  would_update: number;
  already_ok: number;
  conflicts: number;
  match_rate: number;
  risk: "low" | "medium" | "high";
  impact_score: number;
  warnings: string[];
  sample_limit: number;
  matches: ClassificationRuleSimulationMatch[];
}

export type CaseFileStatus = "active" | "waiting" | "done" | "archived";
export interface CaseFileDocument {
  id: number;
  title: string;
  created_at: string | null;
  added_at: string;
  correspondent_name: string | null;
  document_type_name: string | null;
  folder_path: string | null;
  asn: number | null;
  asn_label: string | null;
  page_count: number | null;
}
export interface CaseFile {
  id: number;
  title: string;
  description: string;
  status: CaseFileStatus;
  status_label: string;
  owner: number | null;
  document_count: number;
  latest_document_at: string | null;
  ai_summary: string;
  ai_summary_source: string;
  ai_summary_generated_at: string | null;
  created_at: string;
  updated_at: string;
  documents: CaseFileDocument[];
}
export interface CaseFileSummaryResult {
  case_file: CaseFile;
  summary: string;
  source: string;
  sources: AskSource[];
}
export type DossierStatus = "draft" | "generated" | "final";
export type DossierSource = "local" | "ai" | "unavailable" | "error";
export interface DossierEntity {
  id: number | null;
  kind: string;
  name: string;
  roles: string[];
  sources: string[];
  identifiers: { kind: string; value: string }[];
}
export interface DossierContract {
  id?: number;
  provider?: string;
  contract_number?: string;
  contract_type_label?: string;
  status_label?: string;
  cancel_until?: string | null;
  next_due_on?: string | null;
  ends_on?: string | null;
  sources: string[];
}
export interface DossierTimelineItem {
  document: number;
  title: string;
  folder_path: string | null;
  page: number | null;
  sources: string[];
  reason: string;
}
export interface Dossier {
  id: number;
  title: string;
  query: string;
  status: DossierStatus;
  status_label: string;
  owner: number | null;
  summary: string;
  timeline: DossierTimelineItem[];
  sources: AskSource[];
  entities: DossierEntity[];
  contracts: DossierContract[];
  generated_source: DossierSource;
  generated_source_label: string;
  generated_at: string | null;
  created_at: string;
  updated_at: string;
  document_count: number;
  documents: CaseFileDocument[];
}

export type ContractType =
  | "insurance"
  | "energy"
  | "telecom"
  | "rent"
  | "loan"
  | "subscription"
  | "public"
  | "other";
export type ContractBillingCycle =
  | "monthly"
  | "quarterly"
  | "yearly"
  | "one_time"
  | "unknown";
export type ContractStatus = "active" | "canceled" | "expired" | "unclear";
export type ContractSource = "heuristic" | "ai" | "manual" | "rule";
export interface ContractRecord {
  id: number;
  document: number;
  document_title: string;
  case_file: number | null;
  case_file_title: string | null;
  contract_type: ContractType;
  contract_type_label: string;
  provider: string;
  provider_display: string;
  contract_number: string;
  amount: string | null;
  currency: string;
  billing_cycle: ContractBillingCycle;
  billing_cycle_label: string;
  starts_on: string | null;
  ends_on: string | null;
  notice_period_days: number | null;
  cancel_until: string | null;
  next_due_on: string | null;
  status: ContractStatus;
  status_label: string;
  confidence: number;
  source: ContractSource;
  source_label: string;
  needs_review: boolean;
  extracted_from_version: number | null;
  notes: string;
  reviewed_at: string | null;
  reviewed_by: number | null;
  created_at: string;
  updated_at: string;
}
export interface ContractSummary {
  total: number;
  active: number;
  needs_review: number;
  cancel_soon: number;
  due_soon: number;
  expired: number;
}
export interface ContractScanResult {
  scanned: number;
  created: number;
  updated: number;
  unchanged: number;
  no_contract: number;
  missing: number;
  failed: number;
  errors: { document?: number; error: string }[];
}
export interface ContractQuery {
  status?: ContractStatus | "";
  contract_type?: ContractType | "";
  needs_review?: boolean | "";
}

export type KnowledgeEntityKind =
  | "person"
  | "company"
  | "authority"
  | "iban"
  | "email"
  | "phone"
  | "contract_number"
  | "policy_number"
  | "customer_number"
  | "tax_number"
  | "address"
  | "other";
export type EntitySource =
  | "ocr"
  | "metadata"
  | "mail"
  | "contract"
  | "manual"
  | "heuristic";
export type DocumentEntityRole =
  | "mention"
  | "correspondent"
  | "sender"
  | "recipient"
  | "subject"
  | "contract"
  | "account"
  | "reference";
export type EntityRelationType =
  | "related"
  | "mentioned_with"
  | "uses_identifier"
  | "contract_with"
  | "same_as";
export interface EntityIdentifier {
  id: number;
  entity: number;
  kind: KnowledgeEntityKind;
  kind_label: string;
  value: string;
  normalized_value: string;
  source: EntitySource;
  confidence: number;
  created_at: string;
}
export interface KnowledgeEntity {
  id: number;
  owner: number | null;
  kind: KnowledgeEntityKind;
  kind_label: string;
  name: string;
  canonical_name: string;
  confidence: number;
  source: EntitySource;
  source_label: string;
  metadata: Record<string, unknown>;
  identifiers: EntityIdentifier[];
  document_count: number;
  relation_count: number;
  first_seen_at: string;
  last_seen_at: string;
}
export interface DocumentEntityLink {
  id: number;
  document: number;
  document_title: string;
  entity: number;
  entity_name: string;
  entity_kind: KnowledgeEntityKind;
  entity_kind_label: string;
  role: DocumentEntityRole;
  role_label: string;
  source: EntitySource;
  source_label: string;
  confidence: number;
  occurrences: number;
  source_snippet: string;
  created_at: string;
  updated_at: string;
}
export interface EntityRelation {
  id: number;
  from_entity: number;
  from_name: string;
  from_kind: KnowledgeEntityKind;
  to_entity: number;
  to_name: string;
  to_kind: KnowledgeEntityKind;
  relation_type: EntityRelationType;
  relation_type_label: string;
  document: number | null;
  document_title: string | null;
  confidence: number;
  source: EntitySource;
  source_label: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
export interface KnowledgeSummary {
  total: number;
  by_kind: Partial<Record<KnowledgeEntityKind, number>>;
  documents_linked: number;
}
export interface EntityScanResult {
  scanned: number;
  entities: number;
  links: number;
  relations: number;
  failed: number;
  errors: { document?: number; error: string }[];
}
export interface KnowledgeEntityQuery {
  q?: string;
  kind?: KnowledgeEntityKind | "";
  document?: number | "";
}

export type CaseFileCandidateKind = "existing_case" | "new_case";
export type CaseFileCandidateStatus = "pending" | "applied" | "dismissed";
export interface CaseFileCandidateSignal {
  type: string;
  label?: string;
  value?: string;
  weight?: number;
}
export interface CaseFileCandidate {
  id: number;
  document: number;
  case_file: number | null;
  case_file_title: string | null;
  case_file_status: CaseFileStatus | null;
  kind: CaseFileCandidateKind;
  kind_label: string;
  suggested_title: string;
  score: number;
  reason: string;
  signals: CaseFileCandidateSignal[];
  source: string;
  status: CaseFileCandidateStatus;
  status_label: string;
  created_at: string;
  applied_at: string | null;
  dismissed_at: string | null;
}

// --- Workflow-Engine (STOAA-263) ---
export type WorkflowTriggerType = "document_added" | "document_updated";
export type WorkflowActionType = "assign" | "remove";

export interface WorkflowTrigger {
  id?: number;
  trigger_type: WorkflowTriggerType;
  sources: string; // Komma-getrennt: upload,consume,mail,api
  filter_path: string;
  filter_correspondent: number | null;
  filter_document_type: number | null;
  filter_has_tags: number[];
  filter_has_not_tags: number[];
  filter_text_contains: string;
  filter_text_regex: string;
}

export interface WorkflowAction {
  id?: number;
  order: number;
  action_type: WorkflowActionType;
  assign_title: string;
  assign_correspondent: number | null;
  assign_document_type: number | null;
  assign_storage_path: number | null;
  assign_tags: number[];
  assign_owner: number | null;
  assign_custom_fields: Record<string, unknown>;
  remove_tags: number[];
}

export interface Workflow {
  id: number;
  name: string;
  order: number;
  enabled: boolean;
  trigger: WorkflowTrigger | null;
  actions: WorkflowAction[];
}

export type WorkflowPayload = Omit<Workflow, "id">;
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

export type BackupHealthStatus = "ok" | "warn" | "error";
export type BackupRunStatus = "unknown" | "running" | "success" | "failed";

export interface BackupMonitorEntry {
  status: BackupRunStatus;
  artifact_timestamp: string;
  message: string;
  last_started_at: string | null;
  last_success_at: string | null;
  last_finished_at: string | null;
  updated_at: string | null;
  age_hours: number | null;
  stale: boolean;
}

export interface BackupCronJobStatus {
  name: string;
  schedule: string;
  expected_interval_hours: number;
  alert_after_hours: number;
  last_run_status: BackupRunStatus;
  last_success_at: string | null;
  stale: boolean;
}

export interface BackupStatus {
  status: BackupHealthStatus;
  generated_at: string;
  backup: BackupMonitorEntry;
  cronjob: BackupCronJobStatus;
  restore_drill: BackupMonitorEntry;
}

export interface OCRHealthIssue {
  document_id: number;
  document_title: string;
  version_id: number;
  version_no: number;
  processing_state: ProcessingState;
  processing_error: string;
  processing_failed_step: string;
  processing_failed_at: string | null;
  processing_attempts: number;
  ocr_status: OcrStatus;
  ocr_error: string;
  ocr_started_at: string | null;
  ocr_finished_at: string | null;
  ocr_text_length: number;
  created_at: string | null;
  can_retry: boolean;
}

export interface OCRHealthStatus {
  status: BackupHealthStatus;
  generated_at: string;
  thresholds: {
    ocr_success_rate: number;
    processing_stuck_after_minutes: number;
  };
  summary: {
    total_current_versions: number;
    ocr_success: number;
    ocr_failed: number;
    ocr_running: number;
    ocr_pending: number;
    ocr_skipped: number;
    empty_ocr_text: number;
    ocr_success_rate: number;
    processing_ready: number;
    processing_failed: number;
    retry_pending: number;
    stuck_processing: number;
  };
  oldest_stuck: OCRHealthIssue | null;
  issues: OCRHealthIssue[];
}

export interface OCRRetryResult {
  queued: number;
  limit: number;
  version_ids: number[];
}

export interface SemanticIndexHealth {
  model: string;
  dimension: number;
  documents: number;
  indexed_documents: number;
  missing_documents: number;
  chunks: number;
  generated_at: string;
}

export interface ArchiveHealthIssue {
  document_id: number;
  title: string;
  asn: number;
  archive_status: ArchiveStatus;
  archive_status_label: string;
  archive_checked_at: string | null;
  archive_error: string;
  retention: {
    state: RetentionState;
    retention_until: string | null;
    days_remaining: number | null;
  };
  legal_hold: boolean;
  legal_hold_reason: string;
  current_version: number | null;
}

export interface ArchiveHealthStatus {
  status: BackupHealthStatus;
  generated_at: string;
  thresholds: { retention_due_soon_days: number };
  summary: {
    documents: number;
    archive_ok: number;
    archive_warning: number;
    archive_error: number;
    archive_unchecked: number;
    legal_hold: number;
    retention_active: number;
    retention_due_soon: number;
    retention_expired: number;
  };
  issues: ArchiveHealthIssue[];
}

export interface ArchiveBulkCheckResult {
  checked: number;
  limit: number;
  health: ArchiveHealthStatus;
}

export type EvidenceCheckStatus = "ok" | "warn" | "error";
export interface EvidenceRisk {
  code: string;
  severity: EvidenceCheckStatus;
  message: string;
}
export interface EvidenceCheck {
  code: string;
  status: EvidenceCheckStatus;
  detail: string;
}
export interface EvidenceIssue {
  document_id: number;
  title: string;
  asn: number;
  asn_label: string | null;
  status: EvidenceCheckStatus;
  score: number;
  risks: EvidenceRisk[];
  checks: EvidenceCheck[];
  archive_status: ArchiveStatus;
  archive_status_label: string;
  archive_checked_at: string | null;
  archive_error: string;
  retention: {
    state: RetentionState;
    retention_until: string | null;
    days_remaining: number | null;
  };
  legal_hold: boolean;
  legal_hold_reason: string;
  current_version: number | null;
  processing_state: string | null;
  generated_at: string;
}
export interface EvidenceStatus {
  status: BackupHealthStatus;
  generated_at: string;
  summary: {
    documents: number;
    evidence_ok: number;
    warnings: number;
    errors: number;
    unchecked: number;
    archive_missing: number;
    hash_chain_errors: number;
    seal_missing: number;
    legal_hold: number;
    retention_expired: number;
  };
  issues: EvidenceIssue[];
}
export interface EvidenceVersion {
  id: number;
  version_no: number;
  sha256: string;
  prev_hash: string;
  file_present: boolean;
  archive_present: boolean;
  thumbnail_present: boolean;
  processing_state: string;
  ocr_status: string;
  page_count: number | null;
  size: number;
  is_immutable: boolean;
  metadata_snapshot_present: boolean;
  snapshot_schema_version: number;
  snapshot_taken_at: string | null;
  seal_hash: string;
  seal_ok: boolean;
  created_at: string | null;
}
export interface EvidenceAuditEntry {
  id: number;
  timestamp: string | null;
  actor: string | null;
  action: string;
  object_type: string;
  object_id: string;
  detail: Record<string, unknown>;
}
export interface EvidenceDocumentReport extends EvidenceIssue {
  document_type: string | null;
  correspondent: string | null;
  storage_path: string | null;
  folder: string | null;
  case_file: string | null;
  owner: string | null;
  tags: string[];
  versions: EvidenceVersion[];
  audit: {
    count: number;
    latest: EvidenceAuditEntry[];
  };
  archive_report: Record<string, unknown>;
}

export type DocumentQualityGrade = "excellent" | "good" | "warning" | "critical";
export interface QualityIssue {
  code: string;
  category: "ocr" | "metadata" | "archive" | "review" | "processing" | string;
  severity: EvidenceCheckStatus;
  message: string;
  action: string;
}
export interface DocumentQuality {
  document_id: number;
  title: string;
  asn: number | null;
  asn_label: string | null;
  score: number;
  grade: DocumentQualityGrade;
  status: EvidenceCheckStatus;
  summary: {
    ocr: {
      status: OcrStatus | null;
      status_label: string;
      text_length: number;
      page_count: number | null;
    };
    metadata: {
      completed: number;
      total: number;
      percent: number;
      missing: string[];
    };
    archive: {
      status: ArchiveStatus;
      status_label: string;
      checked_at: string | null;
      error: string;
      archive_file: boolean;
      thumbnail: boolean;
      immutable: boolean;
      sealed: boolean;
      metadata_snapshot: boolean;
    };
    review: {
      status: ReviewStatus;
      status_label: string;
      open_tasks: number;
      top_tasks: {
        kind: ReviewTaskKind;
        kind_label: string;
        message: string;
        priority: number;
      }[];
    };
  };
  issues: QualityIssue[];
  metrics: {
    ocr_text_length: number;
    page_count: number | null;
    open_review_tasks: number;
    metadata_filled: number;
    metadata_total: number;
  };
  archive_status: ArchiveStatus;
  archive_status_label: string;
  processing_state: ProcessingState | null;
  ocr_status: OcrStatus | null;
  added_at: string | null;
  created_at: string | null;
}
export interface QualityStatus {
  status: BackupHealthStatus;
  generated_at: string;
  summary: {
    documents: number;
    average_score: number;
    excellent: number;
    good: number;
    warning: number;
    critical: number;
    ocr_issues: number;
    metadata_issues: number;
    archive_issues: number;
    review_issues: number;
  };
  issues: DocumentQuality[];
}

// --- Freigabelinks (Share-Links, STOAA-190/192) ---
// Verwaltungs-Sicht eines Freigabelinks. Enthält bewusst KEINEN Klartext-Token
// (der kommt einmalig nur aus der Create-Response, siehe ShareLinkCreated).
// ``is_valid`` liefert das Backend read-only (nicht widerrufen UND nicht abgelaufen).
export interface ShareLink {
  id: number;
  document: number;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  is_valid: boolean;
}

// Antwort der Create-API: wie ShareLink, aber mit dem Klartext-Token, der nur
// EINMALIG zurückkommt und danach nie wieder abrufbar ist.
export interface ShareLinkCreated extends ShareLink {
  token: string;
}

// Schmale Nutzer-Auswahl (GET /api/users/, STOAA-221): read-only, admin-only,
// bare Liste der aktiven Nutzer nach Benutzername sortiert. Nur für Zuordnungs-
// Dropdowns (z. B. Standard-Empfänger eines Mailkontos), keine Rechteinfos.
export interface User {
  id: number;
  username: string;
  email: string;
}

// --- Mailkonten (IMAP-Postfächer, STOAA-214/215) ---
// Verwaltungs-Sicht eines Mailkontos. Das Backend liefert das Passwort NIE in
// der Response (write_only); ``password_env``, ``last_checked_at`` und
// ``last_error`` sind read-only (server-/testgepflegt). ``owner`` ist der
// Standard-Empfänger (Nutzer-PK) importierter Dokumente, ``null`` =
// Admin-Triage-Postfach.
export interface MailAccount {
  id: number;
  name: string;
  owner: number | null;
  host: string;
  port: number;
  use_ssl: boolean;
  username: string;
  folder: string;
  enabled: boolean;
  password_env: string;
  // Serverseitig: ``true``, wenn ein Passwort (Klartext-Feld oder ``password_env``)
  // hinterlegt ist – ohne das Passwort preiszugeben. Steuert im Edit-Form den
  // Platzhalter „(unverändert lassen)".
  has_password: boolean;
  last_checked_at: string | null;
  last_error: string;
}

// Nutzlast zum Anlegen/Aktualisieren. ``password`` ist optional: beim Bearbeiten
// bedeutet ein leeres Passwort „unverändert" (Backend löscht es dann nicht).
export interface MailAccountPayload {
  name: string;
  owner: number | null;
  host: string;
  port: number;
  use_ssl: boolean;
  username: string;
  folder: string;
  enabled: boolean;
  password?: string;
}

// Ergebnis des Verbindungstests (POST /mail-accounts/test-connection/). Das
// Backend antwortet auch bei einem fehlgeschlagenen Login mit HTTP 200 – ein
// misslungener Test ist ein erwartetes Ergebnis, kein Client-Fehler. ``ok``
// zeigt Erfolg/Misserfolg, ``message`` ist stets eine anzeigbare Meldung.
export interface MailTestResult {
  ok: boolean;
  message: string;
}

export type ProcessedMailStatus = "imported" | "partial" | "ignored" | "failed";
export interface ProcessedMail {
  id: number;
  account: number;
  account_name: string;
  message_id: string;
  subject: string;
  sender: string;
  received_at: string | null;
  status: ProcessedMailStatus;
  status_label: string;
  attachment_count: number;
  imported_count: number;
  attachment_names: string[];
  imported_documents: CaseFileDocument[];
  note: string;
  error: string;
  processed_at: string;
}
export interface ProcessedMailSummary {
  total: number;
  imported: number;
  partial: number;
  ignored: number;
  failed: number;
  attachments: number;
}
export interface ProcessedMailQuery {
  q?: string;
  status?: ProcessedMailStatus | "";
  account?: number | "";
  page?: number;
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
  // Fachlicher ecoDMS-artiger Ordnerfilter. ``"none"`` zeigt Dokumente ohne Ordner.
  folder?: number | "none" | "";
  case_file?: number | "";
  // Verarbeitungsstatus-Filter (STOAA-248): grober UI-Bucket, leer = kein Filter.
  processing_state?: ProcessingStateFilter | "";
  // Fachlicher Inbox-Filter: offene oder bereits geprüfte Dokumente.
  review_status?: ReviewStatus | "";
  // Triage-Ansicht (STOAA-296): nur ``"none"`` ist wirksam und lädt die
  // owner-losen Dokumente. Ausschließlich für Admins ausgewertet – für
  // Nicht-Admins ignoriert das Backend den Param (Queryset ist ohnehin auf den
  // eigenen Owner isoliert, STOAA-295). Leer = kein Triage-Filter.
  owner?: "none" | "";
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

export type SavedViewQuery = Omit<DocumentQuery, "owner" | "page">;

export interface SavedView {
  id: number;
  name: string;
  description: string;
  query: SavedViewQuery;
  is_default: boolean;
  count: number;
  created_at: string;
  updated_at: string;
}

export interface SavedViewPayload {
  name?: string;
  description?: string;
  query?: SavedViewQuery;
  is_default?: boolean;
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

export function getSavedViews(): Promise<SavedView[]> {
  return listAll<SavedView>("/saved-views/");
}

export function createSavedView(payload: Required<Pick<SavedViewPayload, "name" | "query">> & SavedViewPayload): Promise<SavedView> {
  return postJson<SavedView>("/saved-views/", payload);
}

export async function updateSavedView(
  id: number,
  payload: SavedViewPayload,
): Promise<SavedView> {
  const res = await apiFetch(`/saved-views/${id}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
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

export async function deleteSavedView(id: number): Promise<void> {
  const res = await apiFetch(`/saved-views/${id}/`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Ansicht löschen fehlgeschlagen: HTTP ${res.status}`);
}

export async function getDocument(id: number): Promise<DocumentDetail> {
  const res = await apiFetch(`/documents/${id}/`);
  if (!res.ok) throw new Error(`Dokument laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

// Verarbeitung der aktuellen Version neu anstoßen (STOAA-248). Nur erlaubt, wenn
// ``processing_state === "failed"`` (sonst HTTP 400). Der Retry läuft asynchron;
// die 202-Antwort enthält die aktuelle Version noch im Zustand ``failed`` – der
// Rollup wechselt erst, wenn der Task greift, daher danach das Detail neu laden.
export async function retryProcessing(id: number): Promise<DocumentVersion> {
  const res = await apiFetch(`/documents/${id}/retry_processing/`, {
    method: "POST",
  });
  if (!res.ok) {
    let detail = `Neustart fehlgeschlagen: HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data && typeof data.detail === "string") detail = data.detail;
    } catch {
      /* keine JSON-Fehlermeldung – Fallback bleibt */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function getInboxSummary(): Promise<InboxSummary> {
  const res = await apiFetch("/documents/inbox-summary/");
  if (!res.ok) throw new Error(`Inbox-Status laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function getAutopilotInbox(limit = 25): Promise<AutopilotInbox> {
  const res = await apiFetch(`/documents/autopilot-inbox/?limit=${limit}`);
  if (!res.ok) {
    throw new Error(`Autopilot laden fehlgeschlagen: HTTP ${res.status}`);
  }
  return res.json();
}

// Markiert ein Dokument fachlich als geprüft. Das Backend hält review_status
// bewusst read-only für PATCH; die Review-Bestätigung ist eine eigene Action.
export function markDocumentReviewed(
  id: number,
  options: ReviewLearningOptions = {},
): Promise<DocumentDetail> {
  return postJson<DocumentDetail>(`/documents/${id}/mark_reviewed/`, options);
}

export function markDocumentsReviewed(ids: number[]): Promise<BulkActionResult> {
  return postJson<BulkActionResult>("/documents/mark-reviewed-bulk/", { ids });
}

export function resolveReviewTask(id: number): Promise<ReviewTask> {
  return postJson<ReviewTask>(`/review-tasks/${id}/resolve/`, {
    reason: "frontend_resolve",
  });
}

export function ignoreReviewTask(id: number): Promise<ReviewTask> {
  return postJson<ReviewTask>(`/review-tasks/${id}/ignore/`, {
    reason: "frontend_ignore",
  });
}

export function generateInboxCandidates(
  ids: number[],
): Promise<InboxGenerateCandidatesResult> {
  return postJson<InboxGenerateCandidatesResult>(
    "/documents/inbox-generate-candidates/",
    { ids },
  );
}

export async function getExtractionCandidates(
  id: number,
): Promise<ExtractionCandidate[]> {
  const res = await apiFetch(`/documents/${id}/extraction-candidates/`);
  if (!res.ok) {
    throw new Error(`Vorschläge laden fehlgeschlagen: HTTP ${res.status}`);
  }
  return res.json();
}

export async function generateExtractionCandidates(
  id: number,
): Promise<ExtractionCandidate[]> {
  const res = await apiFetch(`/documents/${id}/extraction-candidates/`, {
    method: "POST",
  });
  if (!res.ok) {
    let detail = `Vorschläge konnten nicht erzeugt werden: HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data && typeof data.detail === "string") detail = data.detail;
    } catch {
      /* keine JSON-Fehlermeldung – Fallback bleibt */
    }
    throw new Error(detail);
  }
  return res.json();
}

export function applyExtractionCandidate(
  documentId: number,
  candidateId: number,
): Promise<ExtractionCandidate> {
  return postJson<ExtractionCandidate>(
    `/documents/${documentId}/extraction-candidates/${candidateId}/apply/`,
    {},
  );
}

export function dismissExtractionCandidate(
  documentId: number,
  candidateId: number,
): Promise<ExtractionCandidate> {
  return postJson<ExtractionCandidate>(
    `/documents/${documentId}/extraction-candidates/${candidateId}/dismiss/`,
    {},
  );
}

export async function getCaseFileCandidates(
  id: number,
): Promise<CaseFileCandidate[]> {
  const res = await apiFetch(`/documents/${id}/case-candidates/`);
  if (!res.ok) {
    throw new Error(`Aktenvorschläge laden fehlgeschlagen: HTTP ${res.status}`);
  }
  return res.json();
}

export async function generateCaseFileCandidates(
  id: number,
): Promise<CaseFileCandidate[]> {
  const res = await apiFetch(`/documents/${id}/case-candidates/`, {
    method: "POST",
  });
  if (!res.ok) {
    let detail = `Aktenvorschläge konnten nicht erzeugt werden: HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data && typeof data.detail === "string") detail = data.detail;
    } catch {
      /* keine JSON-Fehlermeldung – Fallback bleibt */
    }
    throw new Error(detail);
  }
  return res.json();
}

export function applyCaseFileCandidate(
  documentId: number,
  candidateId: number,
): Promise<CaseFileCandidate> {
  return postJson<CaseFileCandidate>(
    `/documents/${documentId}/case-candidates/${candidateId}/apply/`,
    {},
  );
}

export function dismissCaseFileCandidate(
  documentId: number,
  candidateId: number,
): Promise<CaseFileCandidate> {
  return postJson<CaseFileCandidate>(
    `/documents/${documentId}/case-candidates/${candidateId}/dismiss/`,
    {},
  );
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

export async function checkDocumentArchive(id: number): Promise<DocumentArchiveReport> {
  const res = await apiFetch(`/documents/${id}/archive-check/`, { method: "POST" });
  if (!res.ok) throw new Error(`Archivprüfung fehlgeschlagen (HTTP ${res.status})`);
  return res.json();
}

export async function setDocumentLegalHold(
  id: number,
  enabled: boolean,
  reason = "",
): Promise<DocumentDetail> {
  const res = await apiFetch(`/documents/${id}/legal-hold/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled, reason }),
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

export async function getPdfWorkbenchPages(
  id: number,
): Promise<PdfWorkbenchManifest> {
  const res = await apiFetch(`/documents/${id}/pdf-workbench/pages/`);
  if (!res.ok) {
    let detail = `PDF-Seiten laden fehlgeschlagen (HTTP ${res.status})`;
    try {
      const data = await res.json();
      if (data && typeof data.detail === "string") detail = data.detail;
    } catch {
      /* keine JSON-Fehlermeldung */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function getPdfWorkbenchPageThumbnail(
  id: number,
  pageNo: number,
): Promise<Blob> {
  const res = await apiFetch(
    `/documents/${id}/pdf-workbench/pages/${pageNo}/thumbnail/`,
  );
  if (!res.ok) {
    throw new Error(`Seitenminiatur nicht verfügbar (HTTP ${res.status})`);
  }
  return res.blob();
}

export function rewritePdfDocument(
  id: number,
  pages: PdfWorkbenchPageSpec[],
  reason = "",
): Promise<DocumentDetail> {
  return postJson<DocumentDetail>(`/documents/${id}/pdf-workbench/rewrite/`, {
    pages,
    reason,
  });
}

export function mergePdfDocuments(
  id: number,
  documentIds: number[],
  reason = "",
): Promise<DocumentDetail> {
  return postJson<DocumentDetail>(`/documents/${id}/pdf-workbench/merge/`, {
    document_ids: documentIds,
    reason,
  });
}

export function splitPdfDocument(
  id: number,
  parts: PdfWorkbenchSplitPart[],
): Promise<{ documents: DocumentDetail[] }> {
  return postJson<{ documents: DocumentDetail[] }>(
    `/documents/${id}/pdf-workbench/split/`,
    { parts },
  );
}

// Lädt den QR-Code des Dokuments als PNG-Blob (STOAA-284/286). Der Code enthält
// ausschließlich die ASN (``ASN000123``). Per fetch+Blob wegen JWT – ein direkter
// <img src="…/qr/"> würde den Bearer-Token nicht mitschicken.
export async function getDocumentQr(id: number): Promise<Blob> {
  const res = await apiFetch(`/documents/${id}/qr/`);
  if (!res.ok) throw new Error(`QR-Code nicht verfügbar (HTTP ${res.status})`);
  return res.blob();
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

export async function getDocumentRevisionPackage(id: number): Promise<Blob> {
  const res = await apiFetch(`/documents/${id}/revision-package/`);
  if (!res.ok) throw new Error(`Revisionspaket nicht verfügbar (HTTP ${res.status})`);
  return res.blob();
}

export async function getEvidenceStatus(): Promise<EvidenceStatus> {
  const res = await apiFetch("/documents/evidence-status/");
  if (!res.ok) throw new Error(`Beweis-Center laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function getDocumentEvidence(id: number): Promise<EvidenceDocumentReport> {
  const res = await apiFetch(`/documents/${id}/evidence/`);
  if (!res.ok) throw new Error(`Beweisbericht laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function getQualityStatus(): Promise<QualityStatus> {
  const res = await apiFetch("/documents/quality-status/");
  if (!res.ok) {
    throw new Error(`Qualitätscenter laden fehlgeschlagen: HTTP ${res.status}`);
  }
  return res.json();
}

export async function getDocumentQuality(id: number): Promise<DocumentQuality> {
  const res = await apiFetch(`/documents/${id}/quality/`);
  if (!res.ok) {
    throw new Error(`Qualitätsprofil laden fehlgeschlagen: HTTP ${res.status}`);
  }
  return res.json();
}

// --- Versionsvergleich (STOAA-288/289/290 Stufe 1 + STOAA-312/313 Stufe 2) ---
// Contract = ``VersionComparison.to_dict()`` aus ``services/version_compare.py``;
// der Compare-View (``DocumentViewSet.compare_versions``) gibt dieses Dict roh
// zurück – NICHT über den (ungenutzten) ``VersionCompareResultSerializer``.
// Deshalb hier exakt die ``to_dict``-Shape abbilden.
//
// Stufe 2 (STOAA-312) füllt die Metadaten-/Tag-/Feld-Sektionen aus je Version
// gespeicherten ``metadata_snapshot``-Werten. Nur wenn BEIDE verglichenen
// Versionen einen Snapshot tragen, ist ``metadata_versioning_supported: true``
// und die Sektionen sind als ``{added, removed, changed}`` befüllt. Andernfalls
// bleibt das Flag ``false`` und die Sektionen sind leer (Stufe-1-Verhalten).

// Einzelne alt→neu-Änderung eines Wertes.
export interface CompareFieldChange {
  old: string | null;
  new: string | null;
}

export interface CompareSummary {
  text_changed: boolean;
  binary_changed: boolean;
  pages_changed: boolean;
  metadata_changed: boolean;
  tags_changed: boolean;
  custom_fields_changed: boolean;
}

export interface CompareFileDiff {
  old_sha256: string;
  new_sha256: string;
  old_size: number;
  new_size: number;
  old_mime_type: string;
  new_mime_type: string;
  old_page_count: number | null;
  new_page_count: number | null;
  changed: boolean;
  sha256_changed: boolean;
  size_delta: number;
  mime_changed: boolean;
  // Beide Versionen sind PDF (Voraussetzung für Seiten-Diff einer späteren Stufe).
  both_pdf: boolean;
}
export interface ComparePageSummary {
  old_page_count: number | null;
  new_page_count: number | null;
  page_count_changed: boolean;
  added: number;
  removed: number;
  reordered: boolean;
  rotation_changed: boolean;
}

// Sektions-Diff für Metadaten und Zusatzfelder: ``added``/``removed`` sind
// Feldname→Wert-Maps neu hinzugekommener bzw. weggefallener Schlüssel, ``changed``
// je Schlüssel die alt/neu-Werte. Nur bei ``metadata_versioning_supported: true``
// befüllt (sonst liefert das Backend die Leersektion und das Frontend rendert sie
// gar nicht erst).
export interface CompareSectionDiff {
  added: Record<string, string | null>;
  removed: Record<string, string | null>;
  changed: Record<string, CompareFieldChange>;
}

// Tag-Diff trägt volle ``{id, name}``-Objekte (nicht nur Namen) → stabile React-Keys.
export interface CompareTagRef {
  id: number;
  name: string;
}
export interface CompareTagDiff {
  added: CompareTagRef[];
  removed: CompareTagRef[];
}

export interface VersionCompare {
  document: number;
  from_version: number;
  to_version: number;
  change_score: number;
  sections_changed: string[];
  human_summary: string[];
  page_summary: ComparePageSummary;
  summary: CompareSummary;
  text_diff: string;
  // HtmlDiff-Tabelle (difflib). Nur bei tatsächlicher Textänderung gefüllt, sonst
  // "" (dann wird der unified ``text_diff`` zeilenweise gerendert). MUSS vor dem
  // Rendern client-seitig sanitized werden (DOMPurify) – nie roh in
  // dangerouslySetInnerHTML.
  text_diff_html: string;
  metadata: CompareSectionDiff;
  tags: CompareTagDiff;
  custom_fields: CompareSectionDiff;
  files: CompareFileDiff;
  // true nur, wenn beide Versionen einen Metadaten-Snapshot tragen (Stufe 2).
  metadata_versioning_supported: boolean;
}

// Vergleicht zwei Versionen desselben Dokuments (STOAA-288).
// GET /documents/{id}/versions/{from}/compare/{to}/
export async function compareVersions(
  id: number,
  fromVersion: number,
  toVersion: number,
): Promise<VersionCompare> {
  const res = await apiFetch(
    `/documents/${id}/versions/${fromVersion}/compare/${toVersion}/`,
  );
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
  folder?: number | null;
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

export interface BulkUpdatePatch {
  set?: {
    folder?: number | null;
    document_type?: number | null;
    correspondent?: number | null;
    review_status?: ReviewStatus;
  };
  add_tags?: number[];
  remove_tags?: number[];
}
export interface BulkActionResult {
  updated: number;
  unchanged?: number;
  errors: { id?: number; error: string; field?: string }[];
  task_id?: string;
  status?: string;
}

export async function bulkUpdateDocuments(
  ids: number[],
  patch: BulkUpdatePatch,
): Promise<BulkActionResult> {
  return postJson<BulkActionResult>("/documents/bulk-update/", { ids, ...patch });
}

export async function bulkClassifyDocuments(ids: number[]): Promise<BulkActionResult> {
  return postJson<BulkActionResult>("/documents/bulk-classify/", { ids });
}

export interface AskSource {
  id: string;
  document: number;
  document_title: string;
  folder_path: string | null;
  page: number | null;
  snippet: string;
  snippet_html: string;
  score?: number;
  reason?: string;
  source_type?: "page_text" | "ocr_text" | "metadata" | "semantic";
  matched_terms?: string[];
  entities?: {
    id: number;
    kind: KnowledgeEntityKind;
    name: string;
    role: DocumentEntityRole;
    confidence: number;
    identifiers: { kind: KnowledgeEntityKind; value: string }[];
  }[];
  contract?: {
    id: number;
    provider: string;
    contract_number: string;
    contract_type: ContractType;
    contract_type_label: string;
    status: ContractStatus;
    status_label: string;
    amount: string | null;
    currency: string;
    cancel_until: string | null;
    next_due_on: string | null;
    ends_on: string | null;
  } | null;
  case_file?: {
    id: number;
    title: string;
    status: CaseFileStatus;
    status_label: string;
  } | null;
}
export interface AskRetrievalContext {
  query_terms: string[];
  expanded_terms: string[];
  total_candidates: number;
  semantic_candidates?: number;
  filters: {
    folder?: number | string | null;
    case_file?: number | null;
    entity?: number | null;
    contract?: number | null;
    created_from?: string | null;
    created_to?: string | null;
  };
}
export interface SimilarDocument {
  document: number;
  document_title: string;
  folder_path: string | null;
  page: number | null;
  score: number;
  reason: string;
  snippet: string;
  snippet_html: string;
}
export interface SimilarDocumentsResult {
  document: number;
  indexed: boolean;
  model: string;
  results: SimilarDocument[];
}
export type BriefingRiskLevel = "clear" | "low" | "medium" | "high";
export interface DocumentBriefingAction {
  kind: string;
  priority: number;
  title: string;
  description: string;
  action_label: string;
  target:
    | "overview"
    | "briefing"
    | "entities"
    | "similar"
    | "versions"
    | "workbench"
    | "ai"
    | "reminder"
    | "freigabe"
    | "fields"
    | "audit";
}
export interface DocumentBriefing {
  document: {
    id: number;
    title: string;
    asn: number | null;
    asn_label: string | null;
    status: DocumentStatus;
    status_label: string;
    review_status: ReviewStatus;
    review_status_label: string;
    correspondent: string | null;
    document_type: string | null;
    folder: string | null;
    case_file: {
      id: number;
      title: string;
      status: CaseFileStatus;
      status_label: string;
    } | null;
    page_count: number | null;
    added_at: string;
    created_at: string | null;
  };
  summary: { source: "ai_suggestions" | "ocr" | "metadata"; text: string };
  risk_level: BriefingRiskLevel;
  metadata_score: {
    completed: number;
    total: number;
    percent: number;
    missing: string[];
  };
  health: {
    processing_state: ProcessingState | null;
    ocr_status: OcrStatus | null;
    ocr_error: string;
    archive_status: ArchiveStatus;
    archive_status_label: string;
    archive_error: string;
    retention: {
      state: RetentionState;
      retention_until: string | null;
      days_remaining: number | null;
    };
    legal_hold: boolean;
    legal_hold_reason: string;
    sealed: boolean;
    immutable: boolean;
  };
  next_actions: DocumentBriefingAction[];
  risks: { level: "high" | "medium" | "info"; label: string; detail: string }[];
  signals: {
    ocr: { characters: number; words: number; has_text: boolean };
    review_tasks: {
      id: number;
      kind: ReviewTaskKind;
      kind_label: string;
      priority: number;
      message: string;
      suggested_action: string;
    }[];
    reminders: {
      id: number;
      remind_on: string;
      note: string;
      due: boolean;
    }[];
    extraction_candidates: {
      id: number;
      field: ExtractionCandidateField;
      field_label: string;
      value: string;
      confidence: number;
    }[];
    case_candidates: {
      id: number;
      kind: CaseFileCandidateKind;
      kind_label: string;
      target: string;
      score: number;
      reason: string;
    }[];
    contract: {
      id: number;
      provider: string;
      provider_display: string;
      contract_type: ContractType;
      contract_type_label: string;
      contract_number: string;
      amount: string | null;
      currency: string;
      status: ContractStatus;
      status_label: string;
      needs_review: boolean;
      cancel_until: string | null;
      next_due_on: string | null;
      ends_on: string | null;
    } | null;
    ai_suggestions: AiSuggestions;
  };
  timeline: { kind: string; label: string; date: string | null }[];
  relations: {
    entities: {
      id: number;
      name: string;
      kind: KnowledgeEntityKind;
      kind_label: string;
      role: DocumentEntityRole;
      role_label: string;
      confidence: number;
    }[];
    related_documents: {
      id: number;
      title: string;
      reason: string;
      added_at: string;
    }[];
  };
  audit: {
    id: number;
    timestamp: string;
    actor: string | null;
    action: string;
    detail: Record<string, unknown>;
  }[];
  generated_at: string;
}
export interface SemanticReindexResult {
  status: string;
  created: number;
  deleted: number;
  version?: number;
  model?: string;
}
export interface AskResult {
  source: "ai" | "unavailable" | "error" | "retrieval";
  provider?: string;
  answer: string;
  sources: AskSource[];
  retrieval?: AskRetrievalContext;
  error?: string;
}
export type AskFilters = Partial<AskRetrievalContext["filters"]>;
export async function askDocuments(
  question: string,
  folder?: number | "none" | "",
  filters: AskFilters = {},
): Promise<AskResult> {
  return postJson<AskResult>("/ask/", {
    question,
    folder: folder || undefined,
    ...filters,
  });
}

export interface SemanticSearchHit {
  document: number;
  document_title: string;
  folder_path: string | null;
  page: number | null;
  snippet: string;
  snippet_html: string;
  score: number;
  reason?: string;
  source_type?: string;
  matched_terms?: string[];
}
export interface SemanticSearchResult {
  query: string;
  count: number;
  results: SemanticSearchHit[];
  model: string;
  enabled: boolean;
}
/** Bedeutungssuche (pgvector/e5): matcht die Bedeutung der Anfrage, nicht nur Wörter. */
export async function semanticSearch(
  q: string,
  limit = 8,
): Promise<SemanticSearchResult> {
  return postJson<SemanticSearchResult>("/search/semantic/", { q, limit });
}

export interface DuplicateHit {
  document: number;
  title: string;
  score: number;
  kind: "duplicate" | "version";
  added_at: string | null;
  sha256: string | null;
}
export interface DuplicatesResult {
  status: "ok" | "disabled" | "no_embeddings";
  threshold?: number;
  results: DuplicateHit[];
}
/** Inhaltliche Beinah-Duplikate/Versionen eines Dokuments. */
export async function getDocumentDuplicates(id: number): Promise<DuplicatesResult> {
  const res = await apiFetch(`/documents/${id}/duplicates/`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
export interface DuplicatePair {
  a: number;
  a_title: string;
  b: number;
  b_title: string;
  score: number;
  kind: "duplicate" | "version";
}
export interface DuplicateReport {
  status: "ok" | "disabled";
  threshold?: number;
  count: number;
  pairs: DuplicatePair[];
}
/** Korpus-Report: Paare inhaltlicher Beinah-Duplikate im eigenen Bestand. */
export async function getDuplicateReport(): Promise<DuplicateReport> {
  const res = await apiFetch(`/documents/duplicate-report/`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getSimilarDocuments(id: number): Promise<SimilarDocumentsResult> {
  const res = await apiFetch(`/documents/${id}/similar/`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getDocumentBriefing(id: number): Promise<DocumentBriefing> {
  const res = await apiFetch(`/documents/${id}/briefing/`);
  if (!res.ok) throw new Error(`Briefing nicht verfügbar (HTTP ${res.status})`);
  return res.json();
}

export function reindexDocumentEmbeddings(id: number): Promise<SemanticReindexResult> {
  return postJson<SemanticReindexResult>(`/documents/${id}/reindex-semantic/`, {});
}

export interface FilingSuggestionValue {
  id: number;
  label: string;
  confidence: number;
}
export interface FilingTagSuggestion {
  id: number;
  name: string;
  confidence: number;
}
export interface FilingSuggestions {
  status: "ok" | "disabled" | "no_embeddings" | "no_neighbors";
  folder?: FilingSuggestionValue | null;
  correspondent?: FilingSuggestionValue | null;
  document_type?: FilingSuggestionValue | null;
  tags?: FilingTagSuggestion[];
  current?: {
    folder: number | null;
    correspondent: number | null;
    document_type: number | null;
    tags: number[];
  };
  neighbors?: { document: number; title: string; score: number }[];
}
export interface AutoFileBatchResult {
  processed: number;
  filed: number;
  min_confidence: number;
  results: { document: number; title: string; applied: string[]; status?: string }[];
}
/** Batch-Autopilot: sortiert alle noch nicht abgelegten Dokumente vor. */
export function autoFileBatch(minConfidence?: number): Promise<AutoFileBatchResult> {
  return postJson<AutoFileBatchResult>(
    "/documents/auto-file-batch/",
    minConfidence != null ? { min_confidence: minConfidence } : {},
  );
}

/** Auto-Ablage: kNN-Vorschläge (Ordner/Tags/Korrespondent/Typ) aus ähnlichen Dokumenten. */
export async function getFilingSuggestions(id: number): Promise<FilingSuggestions> {
  const res = await apiFetch(`/documents/${id}/filing-suggestions/`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
export interface ApplyFilingResult {
  applied: string[];
  document: DocumentDetail;
}
export async function applyFiling(
  id: number,
  fields?: string[],
): Promise<ApplyFilingResult> {
  return postJson<ApplyFilingResult>(
    `/documents/${id}/apply-filing/`,
    fields ? { fields } : {},
  );
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

export async function getBackupStatus(): Promise<BackupStatus> {
  const res = await apiFetch("/system/backup-status/");
  if (!res.ok) throw new Error(`Backup-Status laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function getOCRHealth(): Promise<OCRHealthStatus> {
  const res = await apiFetch("/system/ocr-health/");
  if (!res.ok) throw new Error(`OCR-Status laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function getSemanticIndexHealth(): Promise<SemanticIndexHealth> {
  const res = await apiFetch("/system/semantic-index/");
  if (!res.ok) throw new Error(`Semantic-Index laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function getArchiveHealth(): Promise<ArchiveHealthStatus> {
  const res = await apiFetch("/system/archive-health/");
  if (!res.ok) throw new Error(`Archivstatus laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function runArchiveBulkCheck(limit = 50): Promise<ArchiveBulkCheckResult> {
  const res = await apiFetch("/system/archive-health/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit }),
  });
  if (!res.ok) throw new Error(`Archivprüfung fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function retryFailedOCRProcessing(limit = 25): Promise<OCRRetryResult> {
  const res = await apiFetch("/system/ocr-health/retry-failed/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit }),
  });
  if (!res.ok) throw new Error(`Retry fehlgeschlagen: HTTP ${res.status}`);
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

// Mobile-Erfassung (STOAA-514/512b): mehrere Kamerafotos in Reihenfolge zu
// EINEM Dokument (Backend fügt sie serverseitig zu einem PDF zusammen, siehe
// STOAA-512a). Jedes File wird als ``images`` angehängt; die Anhänge-Reihenfolge
// bestimmt die Seitenreihenfolge. Content-Type NICHT setzen (Boundary vom
// Browser). Antwort 201 = angelegtes Dokument (DocumentSerializer).
export async function uploadMobileCapture(
  images: File[],
  title?: string,
): Promise<DocumentItem> {
  const form = new FormData();
  for (const img of images) form.append("images", img);
  if (title && title.trim()) form.append("title", title.trim());
  const res = await apiFetch("/documents/mobile-capture/", {
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
export interface FolderRef extends NamedRef {
  parent: number | null;
  full_path: string;
  document_count: number;
}
export async function getFolders(): Promise<FolderRef[]> {
  return listAll<FolderRef>("/folders/");
}

export async function getCaseFiles(): Promise<CaseFile[]> {
  return listAllPages<CaseFile>("/case-files/");
}

export async function getDossiers(): Promise<Dossier[]> {
  return listAllPages<Dossier>("/dossiers/");
}

export async function getContracts(
  query: ContractQuery = {},
): Promise<ContractRecord[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "" && value !== null) {
      params.set(key, String(value));
    }
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return listAllPages<ContractRecord>(`/contracts/${suffix}`);
}

export async function getContractSummary(): Promise<ContractSummary> {
  const res = await apiFetch("/contracts/summary/");
  if (!res.ok) throw new Error(`Vertragsstatus laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export function scanContracts(ids?: number[]): Promise<ContractScanResult> {
  return postJson<ContractScanResult>("/contracts/scan/", ids ? { ids } : {});
}

export function confirmContract(id: number): Promise<ContractRecord> {
  return postJson<ContractRecord>(`/contracts/${id}/confirm/`, {});
}

export async function getKnowledgeEntities(
  query: KnowledgeEntityQuery = {},
): Promise<KnowledgeEntity[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "" && value !== null) {
      params.set(key, String(value));
    }
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return listAllPages<KnowledgeEntity>(`/knowledge-entities/${suffix}`);
}

export async function getKnowledgeSummary(): Promise<KnowledgeSummary> {
  const res = await apiFetch("/knowledge-entities/summary/");
  if (!res.ok) throw new Error(`Gedächtnis-Status laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function getEntityDocuments(id: number): Promise<DocumentItem[]> {
  const res = await apiFetch(`/knowledge-entities/${id}/documents/`);
  if (!res.ok) throw new Error(`Dokumente laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function getEntityRelations(id: number): Promise<EntityRelation[]> {
  const res = await apiFetch(`/knowledge-entities/${id}/relations/`);
  if (!res.ok) throw new Error(`Beziehungen laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export function scanKnowledgeEntities(ids?: number[]): Promise<EntityScanResult> {
  return postJson<EntityScanResult>(
    "/knowledge-entities/scan/",
    ids ? { ids } : {},
  );
}

export function getDocumentEntities(
  documentId: number,
): Promise<DocumentEntityLink[]> {
  return listAllPages<DocumentEntityLink>(`/document-entities/?document=${documentId}`);
}

export async function updateContract(
  id: number,
  payload: Partial<
    Pick<
      ContractRecord,
      | "provider"
      | "contract_number"
      | "amount"
      | "currency"
      | "contract_type"
      | "billing_cycle"
      | "starts_on"
      | "ends_on"
      | "notice_period_days"
      | "cancel_until"
      | "next_due_on"
      | "status"
      | "needs_review"
      | "notes"
      | "case_file"
    >
  >,
): Promise<ContractRecord> {
  const res = await apiFetch(`/contracts/${id}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Vertrag speichern fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export function createCaseFile(payload: {
  title: string;
  description?: string;
  status?: CaseFileStatus;
}): Promise<CaseFile> {
  return postJson<CaseFile>("/case-files/", payload);
}

export function createDossier(payload: {
  title: string;
  query: string;
  status?: DossierStatus;
}): Promise<Dossier> {
  return postJson<Dossier>("/dossiers/", payload);
}

export async function updateDossier(
  id: number,
  payload: Partial<Pick<Dossier, "title" | "query" | "status">>,
): Promise<Dossier> {
  const res = await apiFetch(`/dossiers/${id}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Dossier speichern fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export function generateDossier(id: number): Promise<Dossier> {
  return postJson<Dossier>(`/dossiers/${id}/generate/`, {});
}

export function finalizeDossier(id: number): Promise<Dossier> {
  return postJson<Dossier>(`/dossiers/${id}/finalize/`, {});
}

export async function exportDossierMarkdown(id: number): Promise<Blob> {
  const res = await apiFetch(`/dossiers/${id}/export-markdown/`);
  if (!res.ok) throw new Error(`Dossier-Export fehlgeschlagen: HTTP ${res.status}`);
  return res.blob();
}

export async function updateCaseFile(
  id: number,
  payload: Partial<Pick<CaseFile, "title" | "description" | "status">>,
): Promise<CaseFile> {
  const res = await apiFetch(`/case-files/${id}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Akte speichern fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export function addDocumentsToCaseFile(
  id: number,
  ids: number[],
): Promise<CaseFile> {
  return postJson<CaseFile>(`/case-files/${id}/add-documents/`, { ids });
}

export function removeDocumentsFromCaseFile(
  id: number,
  ids: number[],
): Promise<CaseFile> {
  return postJson<CaseFile>(`/case-files/${id}/remove-documents/`, { ids });
}

export function summarizeCaseFile(id: number): Promise<CaseFileSummaryResult> {
  return postJson<CaseFileSummaryResult>(`/case-files/${id}/summarize/`, {});
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
  rule: ClassificationRulePayload,
): Promise<ClassificationRule> {
  return postJson<ClassificationRule>("/classification-rules/", rule);
}
export function simulateRuleDraft(
  rule: ClassificationRulePayload,
): Promise<ClassificationRuleSimulation> {
  return postJson<ClassificationRuleSimulation>(
    "/classification-rules/simulate/",
    { match: rule.match, then: rule.then },
  );
}
export function simulateExistingRule(
  id: number,
): Promise<ClassificationRuleSimulation> {
  return postJson<ClassificationRuleSimulation>(
    `/classification-rules/${id}/simulate/`,
    {},
  );
}
export async function deleteRule(id: number): Promise<void> {
  const res = await apiFetch(`/classification-rules/${id}/`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) throw new Error(`Löschen fehlgeschlagen: HTTP ${res.status}`);
}

// --- Workflows (STOAA-263) ---
export async function getWorkflows(): Promise<Workflow[]> {
  return listAll<Workflow>("/workflows/");
}
export function createWorkflow(payload: WorkflowPayload): Promise<Workflow> {
  return postJson<Workflow>("/workflows/", payload);
}
export async function updateWorkflow(
  id: number,
  payload: WorkflowPayload,
): Promise<Workflow> {
  const res = await apiFetch(`/workflows/${id}/`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Speichern fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}
export async function deleteWorkflow(id: number): Promise<void> {
  const res = await apiFetch(`/workflows/${id}/`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) throw new Error(`Löschen fehlgeschlagen: HTTP ${res.status}`);
}

// --- Fristen-Center / Timeline ---
export type TimelineSource =
  | "reminder"
  | "contract"
  | "review_task"
  | "approval"
  | "retention";
export type TimelineBucket = "overdue" | "today" | "soon" | "upcoming";
export type TimelineSeverity = "high" | "medium" | "low" | "info";
export interface TimelineItem {
  id: string;
  source: TimelineSource;
  source_id: number;
  kind: string;
  title: string;
  description: string;
  date: string;
  days_delta: number;
  bucket: TimelineBucket;
  severity: TimelineSeverity;
  document: number;
  document_title: string;
  action_label: string;
  metadata: Record<string, unknown>;
}
export interface TimelineResult {
  generated_at: string;
  today: string;
  horizon: string;
  days: number;
  summary: {
    total: number;
    overdue: number;
    today: number;
    soon: number;
    upcoming: number;
    high: number;
    medium: number;
    low: number;
    by_source: Partial<Record<TimelineSource, number>>;
  };
  buckets: Record<TimelineBucket, TimelineItem[]>;
  items: TimelineItem[];
}
export async function getTimeline(days?: number): Promise<TimelineResult> {
  const suffix = days === undefined ? "" : `?days=${days}`;
  const res = await apiFetch(`/timeline/${suffix}`);
  if (!res.ok) throw new Error(`Fristen laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}
export async function downloadTimelineIcs(days?: number): Promise<Blob> {
  const suffix = days === undefined ? "" : `?days=${days}`;
  const res = await apiFetch(`/timeline/ics/${suffix}`);
  if (!res.ok) throw new Error(`Kalenderexport fehlgeschlagen: HTTP ${res.status}`);
  return res.blob();
}

// --- Wiedervorlage/Erinnerungen (STOAA-372/374) ---
// Owner-gescopet über /api/reminders/ (nur Erinnerungen zu eigenen Dokumenten,
// DMS-Admin sieht alle). ``remind_on`` ist ein reines Datum ("YYYY-MM-DD"),
// ``created_by``/``notified_at`` sind read-only (Server bzw. Beat).
export interface Reminder {
  id: number;
  document: number;
  remind_on: string; // Datum "YYYY-MM-DD"
  note: string;
  done: boolean;
  created_by: number | null;
  notified_at: string | null; // ISO oder null
  created_at: string;
  updated_at: string;
}

// Struktur von GET /api/reminders/due/?days=N (nicht paginiert, siehe STOAA-373).
export interface DueReminders {
  faellig: Reminder[]; // remind_on <= heute (überfällig/heute)
  anstehend: Reminder[]; // heute < remind_on <= heute+N
}

// Nutzlast beim Anlegen/Ändern. ``document`` nur beim Anlegen relevant.
export interface ReminderInput {
  document?: number;
  remind_on?: string;
  note?: string;
  done?: boolean;
}

// Erinnerungen laden – optional auf ein Dokument gefiltert (client-seitig, da
// das Backend die Liste nicht nach ``document`` filtert). Folgt der Paginierung.
export async function listReminders(documentId?: number): Promise<Reminder[]> {
  const all = await listAllPages<Reminder>("/reminders/");
  return documentId === undefined
    ? all
    : all.filter((r) => r.document === documentId);
}
export function createReminder(input: ReminderInput): Promise<Reminder> {
  return postJson<Reminder>("/reminders/", input);
}
export async function updateReminder(
  id: number,
  input: ReminderInput,
): Promise<Reminder> {
  const res = await apiFetch(`/reminders/${id}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Speichern fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}
export async function deleteReminder(id: number): Promise<void> {
  const res = await apiFetch(`/reminders/${id}/`, { method: "DELETE" });
  if (!res.ok && res.status !== 204)
    throw new Error(`Löschen fehlgeschlagen: HTTP ${res.status}`);
}
export function markReminderDone(id: number): Promise<Reminder> {
  return postJson<Reminder>(`/reminders/${id}/done/`, {});
}
export async function getDueReminders(days?: number): Promise<DueReminders> {
  const suffix = days === undefined ? "" : `?days=${days}`;
  const res = await apiFetch(`/reminders/due/${suffix}`);
  if (!res.ok) throw new Error(`Laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

// --- Mailkonten (STOAA-214/215) ---
// CRUD + Verbindungstest unter /api/mail-accounts/. Nur für DMS-Admins
// (Backend-Permission ``IsDmsAdmin``); Nicht-Admins erhalten 403 (im FE wird der
// Menüpunkt gar nicht erst gezeigt). Passwort geht nur rein (write_only), nie
// zurück.
export async function getMailAccounts(): Promise<MailAccount[]> {
  return listAll<MailAccount>("/mail-accounts/");
}

// Aktive Nutzer für Zuordnungs-Dropdowns (admin-only im Backend). Bare Liste.
export async function getUsers(): Promise<User[]> {
  return listAll<User>("/users/");
}

// Owner eines (Triage-)Dokuments setzen (STOAA-295/296). Nur für Admins –
// das Backend erzwingt ``IsDmsAdmin`` (403 für Normalnutzer). Body ``{owner}``
// erwartet die Nutzer-ID; die Antwort ist das aktualisierte Dokument. Nach
// Erfolg fällt das Dokument aus der ``?owner=none``-Liste heraus.
export async function setDocumentOwner(
  id: number,
  owner: number,
): Promise<DocumentDetail> {
  const res = await apiFetch(`/documents/${id}/set-owner/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner }),
  });
  if (!res.ok) {
    let detail = `Zuweisen fehlgeschlagen: HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data && typeof data.detail === "string") detail = data.detail;
    } catch {
      /* keine JSON-Fehlermeldung – Fallback bleibt */
    }
    throw new Error(detail);
  }
  return res.json();
}
export function createMailAccount(
  payload: MailAccountPayload,
): Promise<MailAccount> {
  return postJson<MailAccount>("/mail-accounts/", payload);
}
// PATCH: nur die übergebenen Felder ändern. Leeres/ausgelassenes ``password``
// lässt das gespeicherte Passwort unverändert (Backend-Verhalten).
export async function updateMailAccount(
  id: number,
  payload: Partial<MailAccountPayload>,
): Promise<MailAccount> {
  const res = await apiFetch(`/mail-accounts/${id}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
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
export async function deleteMailAccount(id: number): Promise<void> {
  const res = await apiFetch(`/mail-accounts/${id}/`, { method: "DELETE" });
  if (res.ok || res.status === 204) return;
  let detail = `HTTP ${res.status}`;
  try {
    const data = await res.json();
    detail = data.detail || JSON.stringify(data);
  } catch {
    /* keine JSON-Fehlermeldung */
  }
  throw new Error(detail);
}
// Echter IMAP-Login-Test des gespeicherten Kontos. Der Endpoint liegt auf der
// Collection (``test-connection``, nicht am Detail-Objekt); das gewünschte Konto
// wird per ``{ id }`` im Body adressiert. Der Test ist zustandslos – er
// speichert nichts (kein ``last_checked_at``-Update), sondern liefert nur das
// Ergebnis-Banner zurück. Fehlschläge kommen mit HTTP 200 und ``ok: false``;
// nur echte HTTP-Fehler (403/404/500) werfen.
export async function testMailAccount(id: number): Promise<MailTestResult> {
  const res = await apiFetch(`/mail-accounts/test-connection/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
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

export async function getProcessedMails(
  query: ProcessedMailQuery = {},
): Promise<Paginated<ProcessedMail>> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "" && value !== null) {
      params.set(key, String(value));
    }
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const res = await apiFetch(`/processed-mails/${suffix}`);
  if (!res.ok) throw new Error(`Mails laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export async function getProcessedMailSummary(): Promise<ProcessedMailSummary> {
  const res = await apiFetch("/processed-mails/summary/");
  if (!res.ok) throw new Error(`Mail-Status laden fehlgeschlagen: HTTP ${res.status}`);
  return res.json();
}

export function markProcessedMailIgnored(
  id: number,
  note = "",
): Promise<ProcessedMail> {
  return postJson<ProcessedMail>(`/processed-mails/${id}/mark-ignored/`, { note });
}

// --- Freigabelinks (STOAA-192) ---
// Erstellt einen Freigabelink mit Pflicht-Ablauf. ``expires_at`` als ISO-8601
// (UTC). Die Response enthält den Klartext-Token EINMALIG (Feld ``token``).
export function createShareLink(
  document: number,
  expires_at: string,
): Promise<ShareLinkCreated> {
  return postJson<ShareLinkCreated>("/document-share-links/", {
    document,
    expires_at,
  });
}
// Listet die Freigabelinks eines Dokuments (owner-gescoped im Backend).
export async function getShareLinks(document: number): Promise<ShareLink[]> {
  return listAll<ShareLink>(`/document-share-links/?document=${document}`);
}
// Widerruft einen Link (Soft-Delete): setzt ``revoked_at``. Backend liefert den
// aktualisierten Datensatz (is_valid=false) zurück.
export async function revokeShareLink(id: number): Promise<ShareLink> {
  const res = await apiFetch(`/document-share-links/${id}/`, { method: "DELETE" });
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
export const createFolder = (name: string, parent: number | null = null) =>
  postJson<FolderRef>("/folders/", { name, parent });

// Hilfsfunktion: paginierte Liste in ein flaches Array einsammeln (erste Seite genügt hier).
async function listAll<T>(path: string): Promise<T[]> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(`Laden fehlgeschlagen: HTTP ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : data.results;
}

// Wie listAll, folgt aber der Paginierung über alle Seiten (``next``). Nötig,
// wo client-seitig gefiltert wird (z. B. Erinnerungen je Dokument), damit nicht
// nur die erste Seite (PAGE_SIZE) berücksichtigt wird.
async function listAllPages<T>(path: string): Promise<T[]> {
  const out: T[] = [];
  let next: string | null = path;
  while (next) {
    const res = await apiFetch(next);
    if (!res.ok) throw new Error(`Laden fehlgeschlagen: HTTP ${res.status}`);
    const data = await res.json();
    if (Array.isArray(data)) return data; // unpaginiert
    out.push(...(data.results as T[]));
    // ``next`` ist eine absolute URL – in einen /api-relativen Pfad umwandeln,
    // da apiFetch selbst API_BASE ("/api") voranstellt.
    if (data.next) {
      const u = new URL(data.next);
      next = u.pathname.replace(/^\/api/, "") + u.search;
    } else {
      next = null;
    }
  }
  return out;
}
