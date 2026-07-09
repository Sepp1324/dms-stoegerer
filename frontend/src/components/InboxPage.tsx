import { useEffect, useState } from "react";
import {
  applyCaseFileCandidate,
  applyExtractionCandidate,
  dismissCaseFileCandidate,
  dismissExtractionCandidate,
  generateInboxCandidates,
  generateCaseFileCandidates,
  generateExtractionCandidates,
  getCaseFileCandidates,
  getDocument,
  getDocuments,
  getDocumentThumbnail,
  getExtractionCandidates,
  getInboxSummary,
  ignoreReviewTask,
  markDocumentReviewed,
  markDocumentsReviewed,
  resolveReviewTask,
  type CaseFileCandidate,
  type DocumentItem,
  type ExtractionCandidate,
  type InboxSummary,
  type ReviewTask,
  type ReviewLearningOptions,
} from "../api";
import { ProcessingBadge } from "./ProcessingStatus";
import { sanitizeSnippet } from "../sanitize";

function Thumb({ doc }: { doc: DocumentItem }) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let url: string | null = null;
    getDocumentThumbnail(doc.id)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => {
        /* Noch kein Thumbnail vorhanden: Icon-Fallback reicht für die Inbox. */
      });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [doc.id]);

  return (
    <div className="inbox-thumb" aria-hidden="true">
      {src ? (
        <img src={src} alt="" />
      ) : (
        <svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true">
          <path
            fill="currentColor"
            d="M6 2h7l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2m7 1.5V8h4.5z"
          />
        </svg>
      )}
    </div>
  );
}

function formatOldest(value: string | null): string {
  if (!value) return "—";
  const ageMs = Date.now() - new Date(value).getTime();
  const hours = Math.max(0, Math.round(ageMs / 36e5));
  if (hours < 24) return `${hours || 1}h`;
  return `${Math.round(hours / 24)}d`;
}

function SummaryCard({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: number | string;
  tone?: "default" | "warn" | "error" | "ok";
}) {
  return (
    <div className={`inbox-metric inbox-metric--${tone}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

export default function InboxPage({
  canEdit,
  onOpenDocument,
}: {
  canEdit: boolean;
  onOpenDocument: (id: number) => void;
}) {
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [count, setCount] = useState(0);
  const [summary, setSummary] = useState<InboxSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkBusy, setBulkBusy] = useState<string | null>(null);
  const [bulkMessage, setBulkMessage] = useState<string | null>(null);
  const [learnTextByDoc, setLearnTextByDoc] = useState<Record<number, string>>({});
  const [taskBusy, setTaskBusy] = useState<number | null>(null);
  const [candidatesByDoc, setCandidatesByDoc] = useState<
    Record<number, ExtractionCandidate[]>
  >({});
  const [candidateBusy, setCandidateBusy] = useState<string | null>(null);
  const [caseCandidatesByDoc, setCaseCandidatesByDoc] = useState<
    Record<number, CaseFileCandidate[]>
  >({});
  const [caseCandidateBusy, setCaseCandidateBusy] = useState<string | null>(null);

  async function refreshSummary() {
    try {
      setSummary(await getInboxSummary());
    } catch {
      /* Die Dokumentliste ist wichtiger; Summary bleibt beim letzten Stand. */
    }
  }

  function load() {
    setLoading(true);
    setError(null);
    setBulkMessage(null);
    Promise.all([
      getDocuments({
        review_status: "needs_review",
        processing_state: "ready",
        ordering: "-added_at",
      }),
      getInboxSummary(),
    ])
      .then(([res, nextSummary]) => {
        setDocs(res.results);
        setCount(res.count);
        setSummary(nextSummary);
        setSelectedIds((current) => {
          const visible = new Set(res.results.map((doc) => doc.id));
          return new Set([...current].filter((id) => visible.has(id)));
        });
        void loadCandidates(res.results);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  const visibleIds = docs.map((doc) => doc.id);
  const selectedVisibleIds = visibleIds.filter((id) => selectedIds.has(id));
  const allVisibleSelected =
    docs.length > 0 && visibleIds.every((id) => selectedIds.has(id));

  function toggleDocument(docId: number) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(docId)) {
        next.delete(docId);
      } else {
        next.add(docId);
      }
      return next;
    });
  }

  function toggleAllVisible() {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (allVisibleSelected) {
        visibleIds.forEach((id) => next.delete(id));
      } else {
        visibleIds.forEach((id) => next.add(id));
      }
      return next;
    });
  }

  async function loadCandidates(items: DocumentItem[]) {
    const next: Record<number, ExtractionCandidate[]> = {};
    const nextCases: Record<number, CaseFileCandidate[]> = {};
    await Promise.all(
      items.map(async (doc) => {
        try {
          next[doc.id] = await getExtractionCandidates(doc.id);
        } catch {
          next[doc.id] = [];
        }
        try {
          nextCases[doc.id] = await getCaseFileCandidates(doc.id);
        } catch {
          nextCases[doc.id] = [];
        }
      }),
    );
    setCandidatesByDoc(next);
    setCaseCandidatesByDoc(nextCases);
  }

  async function markReviewed(docId: number, options: ReviewLearningOptions = {}) {
    setSavingId(docId);
    setError(null);
    setBulkMessage(null);
    try {
      await markDocumentReviewed(docId, options);
      setDocs((current) => current.filter((doc) => doc.id !== docId));
      setSelectedIds((current) => {
        const next = new Set(current);
        next.delete(docId);
        return next;
      });
      setCount((current) => Math.max(0, current - 1));
      await refreshSummary();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingId(null);
    }
  }

  async function refreshDocCandidates(docId: number) {
    const list = await getExtractionCandidates(docId);
    setCandidatesByDoc((current) => ({ ...current, [docId]: list }));
  }

  async function refreshDocumentCard(docId: number) {
    try {
      const detail = await getDocument(docId);
      setDocs((current) =>
        current.map((doc) =>
          doc.id === docId
            ? {
                ...doc,
                correspondent: detail.correspondent,
                correspondent_name: detail.correspondent_name,
                document_type: detail.document_type,
                document_type_name: detail.document_type_name,
                folder: detail.folder,
                folder_name: detail.folder_name,
                folder_path: detail.folder_path,
                case_file: detail.case_file,
                case_file_title: detail.case_file_title,
                review_task_count: detail.review_task_count,
                review_tasks: detail.review_tasks,
              }
            : doc,
        ),
      );
    } catch {
      /* Karte bleibt unverändert; der nächste Reload zieht den frischen Stand. */
    }
  }

  async function generateForDoc(docId: number) {
    const key = `generate:${docId}`;
    setCandidateBusy(key);
    setError(null);
    try {
      const list = await generateExtractionCandidates(docId);
      setCandidatesByDoc((current) => ({ ...current, [docId]: list }));
      await refreshDocumentCard(docId);
      await refreshSummary();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCandidateBusy(null);
    }
  }

  async function applyCandidate(docId: number, candidateId: number) {
    const key = `apply:${candidateId}`;
    setCandidateBusy(key);
    setError(null);
    try {
      await applyExtractionCandidate(docId, candidateId);
      await refreshDocCandidates(docId);
      await refreshDocumentCard(docId);
      await refreshSummary();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCandidateBusy(null);
    }
  }

  async function dismissCandidate(docId: number, candidateId: number) {
    const key = `dismiss:${candidateId}`;
    setCandidateBusy(key);
    setError(null);
    try {
      await dismissExtractionCandidate(docId, candidateId);
      await refreshDocCandidates(docId);
      await refreshDocumentCard(docId);
      await refreshSummary();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCandidateBusy(null);
    }
  }

  async function refreshCaseCandidates(docId: number) {
    const list = await getCaseFileCandidates(docId);
    setCaseCandidatesByDoc((current) => ({ ...current, [docId]: list }));
  }

  async function generateCaseCandidatesForDoc(docId: number) {
    const key = `generate-case:${docId}`;
    setCaseCandidateBusy(key);
    setError(null);
    try {
      const list = await generateCaseFileCandidates(docId);
      setCaseCandidatesByDoc((current) => ({ ...current, [docId]: list }));
      await refreshDocumentCard(docId);
      await refreshSummary();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCaseCandidateBusy(null);
    }
  }

  async function applyCaseCandidate(docId: number, candidateId: number) {
    const key = `apply-case:${candidateId}`;
    setCaseCandidateBusy(key);
    setError(null);
    try {
      const candidate = await applyCaseFileCandidate(docId, candidateId);
      await refreshCaseCandidates(docId);
      await refreshDocumentCard(docId);
      setDocs((current) =>
        current.map((doc) =>
          doc.id === docId
            ? {
                ...doc,
                case_file: candidate.case_file,
                case_file_title:
                  candidate.case_file_title || candidate.suggested_title || null,
              }
            : doc,
        ),
      );
      await refreshSummary();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCaseCandidateBusy(null);
    }
  }

  async function dismissCaseCandidate(docId: number, candidateId: number) {
    const key = `dismiss-case:${candidateId}`;
    setCaseCandidateBusy(key);
    setError(null);
    try {
      await dismissCaseFileCandidate(docId, candidateId);
      await refreshCaseCandidates(docId);
      await refreshDocumentCard(docId);
      await refreshSummary();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCaseCandidateBusy(null);
    }
  }

  async function bulkGenerateCandidates(useAllVisible = false) {
    const ids = useAllVisible ? visibleIds : selectedVisibleIds;
    if (ids.length === 0) return;
    setBulkBusy("generate");
    setError(null);
    setBulkMessage(null);
    try {
      const result = await generateInboxCandidates(ids);
      await loadCandidates(docs);
      load();
      await refreshSummary();
      const created = result.extraction_created + result.case_created;
      setBulkMessage(
        `${created} Vorschläge für ${result.documents} Dokumente erzeugt.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBulkBusy(null);
    }
  }

  async function bulkMarkReviewed() {
    if (selectedVisibleIds.length === 0) return;
    setBulkBusy("review");
    setError(null);
    setBulkMessage(null);
    try {
      const result = await markDocumentsReviewed(selectedVisibleIds);
      setDocs((current) =>
        current.filter((doc) => !selectedVisibleIds.includes(doc.id)),
      );
      setSelectedIds(new Set());
      setCount((current) => Math.max(0, current - result.updated));
      await refreshSummary();
      setBulkMessage(`${result.updated} Dokumente als geprüft markiert.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBulkBusy(null);
    }
  }

  async function finishTask(task: ReviewTask, action: "resolve" | "ignore") {
    setTaskBusy(task.id);
    setError(null);
    setBulkMessage(null);
    try {
      if (action === "resolve") {
        await resolveReviewTask(task.id);
      } else {
        await ignoreReviewTask(task.id);
      }
      setDocs((current) =>
        current.map((doc) =>
          doc.id === task.document
            ? {
                ...doc,
                review_tasks: doc.review_tasks.filter((item) => item.id !== task.id),
                review_task_count: Math.max(0, doc.review_task_count - 1),
              }
            : doc,
        ),
      );
      await refreshSummary();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setTaskBusy(null);
    }
  }

  if (loading) {
    return (
      <section className="inbox">
        <div className="inbox-head">
          <p className="eyebrow">Review-Queue</p>
          <h2>Offene Dokumente werden geladen</h2>
        </div>
        <div className="inbox-list">
          {Array.from({ length: 4 }).map((_, i) => (
            <div className="inbox-row inbox-row--skeleton" key={i} />
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className="inbox">
      <div className="inbox-head">
        <div>
          <p className="eyebrow">Mailroom</p>
          <h2>{count} offene {count === 1 ? "Prüfung" : "Prüfungen"}</h2>
        </div>
        <button type="button" className="link" onClick={load}>
          Aktualisieren
        </button>
      </div>

      {summary && (
        <div className="inbox-metrics" aria-label="Inbox-Status">
          <SummaryCard label="bereit" value={summary.ready} tone="ok" />
          <SummaryCard label="in Arbeit" value={summary.processing} />
          <SummaryCard label="fehlerhaft" value={summary.failed} tone="error" />
          <SummaryCard
            label="Klärungen"
            value={summary.open_review_tasks}
            tone={summary.open_review_tasks > 0 ? "warn" : "ok"}
          />
          <SummaryCard
            label="Vorschläge"
            value={
              summary.pending_extraction_candidates +
              summary.pending_case_candidates +
              summary.with_ai_suggestions
            }
            tone="warn"
          />
          <SummaryCard label="ältester Eingang" value={formatOldest(summary.oldest_added_at)} />
        </div>
      )}

      {error && (
        <div className="state state--error">
          <strong>Inbox konnte nicht aktualisiert werden.</strong>
          <span>{error}</span>
        </div>
      )}
      {bulkMessage && (
        <div className="status status--ok" role="status">
          {bulkMessage}
        </div>
      )}

      {docs.length === 0 ? (
        <div className="state">
          <strong>Alles geprüft.</strong>
          <span>Neue fertige Dokumente erscheinen hier automatisch.</span>
        </div>
      ) : (
        <>
          {canEdit && (
            <div className="inbox-toolbar">
              <label className="inbox-toolbar__select">
                <input
                  type="checkbox"
                  checked={allVisibleSelected}
                  onChange={toggleAllVisible}
                />
                <span>{selectedVisibleIds.length} ausgewählt</span>
              </label>
              <div className="inbox-toolbar__actions">
                <button
                  type="button"
                  onClick={() => bulkGenerateCandidates(false)}
                  disabled={selectedVisibleIds.length === 0 || bulkBusy === "generate"}
                >
                  {bulkBusy === "generate" ? "Erzeuge …" : "Vorschläge erzeugen"}
                </button>
                <button
                  type="button"
                  className="link"
                  onClick={() => bulkGenerateCandidates(true)}
                  disabled={docs.length === 0 || bulkBusy === "generate"}
                >
                  Für sichtbare erzeugen
                </button>
                <button
                  type="button"
                  onClick={bulkMarkReviewed}
                  disabled={selectedVisibleIds.length === 0 || bulkBusy === "review"}
                >
                  {bulkBusy === "review" ? "Speichere …" : "Auswahl geprüft"}
                </button>
              </div>
            </div>
          )}
          <div className="inbox-list">
            {docs.map((doc) => {
              const learnText = learnTextByDoc[doc.id] ?? "";
              return (
                <article
                  className={`inbox-row ${canEdit ? "inbox-row--selectable" : ""}`}
                  key={doc.id}
                >
              {canEdit && (
                <label className="inbox-row__check" title="Dokument auswählen">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(doc.id)}
                    onChange={() => toggleDocument(doc.id)}
                  />
                </label>
              )}
              <Thumb doc={doc} />
              <div className="inbox-row__main">
                <button
                  type="button"
                  className="inbox-row__title"
                  onClick={() => onOpenDocument(doc.id)}
                  title={doc.title}
                >
                  {doc.title}
                </button>
                <p className="inbox-row__meta">
                  {doc.correspondent_name ?? "Unbekannter Korrespondent"}
                  {doc.document_type_name ? ` · ${doc.document_type_name}` : ""}
                  {doc.case_file_title ? ` · Akte: ${doc.case_file_title}` : ""}
                </p>
                {doc.tags.length > 0 && (
                  <div className="inbox-row__tags">
                    {doc.tags.slice(0, 4).map((tag) => (
                      <span
                        key={tag.id}
                        className="tag"
                        style={{ borderColor: tag.color, color: tag.color }}
                      >
                        {tag.name}
                      </span>
                    ))}
                  </div>
                )}
                {doc.review_tasks.length > 0 ? (
                  <div className="review-tasks">
                    {doc.review_tasks.slice(0, 4).map((task) => (
                      <div className="review-task" key={task.id}>
                        <div className="review-task__head">
                          <span>{task.kind_label}</span>
                          <small>Priorität {task.priority}</small>
                        </div>
                        <strong>{task.message}</strong>
                        {task.suggested_action && <p>{task.suggested_action}</p>}
                        {canEdit && (
                          <div className="review-task__actions">
                            <button
                              type="button"
                              onClick={() => finishTask(task, "resolve")}
                              disabled={taskBusy === task.id}
                            >
                              Erledigt
                            </button>
                            <button
                              type="button"
                              className="link"
                              onClick={() => finishTask(task, "ignore")}
                              disabled={taskBusy === task.id}
                            >
                              Ignorieren
                            </button>
                          </div>
                        )}
                      </div>
                    ))}
                    {doc.review_tasks.length > 4 && (
                      <span className="review-tasks__more">
                        +{doc.review_tasks.length - 4} weitere
                      </span>
                    )}
                  </div>
                ) : (
                  <div className="review-tasks review-tasks--empty">
                    <span>Keine konkreten Klärungspunkte offen.</span>
                  </div>
                )}
                <div className="smart-candidates">
                  {(candidatesByDoc[doc.id] ?? [])
                    .filter((candidate) => candidate.status === "pending")
                    .map((candidate) => (
                      <div className="smart-candidate" key={candidate.id}>
                        <div className="smart-candidate__head">
                          <span className="smart-candidate__field">
                            {candidate.field_label}
                          </span>
                          <strong>{candidate.normalized_value || candidate.value}</strong>
                          <span className="smart-candidate__confidence">
                            {candidate.confidence}%
                          </span>
                        </div>
                        <p className="smart-candidate__reason">
                          {candidate.reason}
                          {candidate.source_page
                            ? ` · Seite ${candidate.source_page}`
                            : ""}
                        </p>
                        {(candidate.source_snippet_html ||
                          candidate.source_snippet) && (
                          <p
                            className="smart-candidate__snippet"
                            dangerouslySetInnerHTML={{
                              __html: sanitizeSnippet(
                                candidate.source_snippet_html ||
                                  candidate.source_snippet,
                              ),
                            }}
                          />
                        )}
                        {canEdit && (
                          <div className="smart-candidate__actions">
                            <button
                              type="button"
                              onClick={() => applyCandidate(doc.id, candidate.id)}
                              disabled={candidateBusy === `apply:${candidate.id}`}
                            >
                              Übernehmen
                            </button>
                            <button
                              type="button"
                              className="link"
                              onClick={() => dismissCandidate(doc.id, candidate.id)}
                              disabled={candidateBusy === `dismiss:${candidate.id}`}
                            >
                              Verwerfen
                            </button>
                          </div>
                        )}
                      </div>
                    ))}
                  {canEdit &&
                    (candidatesByDoc[doc.id] ?? []).filter(
                      (candidate) => candidate.status === "pending",
                    ).length === 0 && (
                      <button
                        type="button"
                        className="link smart-candidates__generate"
                        onClick={() => generateForDoc(doc.id)}
                        disabled={candidateBusy === `generate:${doc.id}`}
                      >
                        {candidateBusy === `generate:${doc.id}`
                          ? "Erkenne …"
                          : "Strukturdaten erkennen"}
                      </button>
                    )}
                </div>
                <div className="smart-candidates smart-candidates--case">
                  {(caseCandidatesByDoc[doc.id] ?? [])
                    .filter((candidate) => candidate.status === "pending")
                    .map((candidate) => (
                      <div
                        className="smart-candidate smart-candidate--case"
                        key={candidate.id}
                      >
                        <div className="smart-candidate__head">
                          <span className="smart-candidate__field">
                            {candidate.kind_label}
                          </span>
                          <strong>
                            {candidate.case_file_title ||
                              candidate.suggested_title ||
                              "Neue Akte"}
                          </strong>
                          <span className="smart-candidate__confidence">
                            {candidate.score}%
                          </span>
                        </div>
                        <p className="smart-candidate__reason">
                          {candidate.reason}
                        </p>
                        {candidate.signals.length > 0 && (
                          <div className="case-signals">
                            {candidate.signals.slice(0, 4).map((signal, idx) => (
                              <span key={`${candidate.id}-${signal.type}-${idx}`}>
                                {signal.label || signal.type}
                                {signal.value ? `: ${signal.value}` : ""}
                              </span>
                            ))}
                          </div>
                        )}
                        {canEdit && (
                          <div className="smart-candidate__actions">
                            <button
                              type="button"
                              onClick={() =>
                                applyCaseCandidate(doc.id, candidate.id)
                              }
                              disabled={
                                caseCandidateBusy === `apply-case:${candidate.id}`
                              }
                            >
                              Zur Akte
                            </button>
                            <button
                              type="button"
                              className="link"
                              onClick={() =>
                                dismissCaseCandidate(doc.id, candidate.id)
                              }
                              disabled={
                                caseCandidateBusy === `dismiss-case:${candidate.id}`
                              }
                            >
                              Verwerfen
                            </button>
                          </div>
                        )}
                      </div>
                    ))}
                  {canEdit &&
                    !doc.case_file &&
                    (caseCandidatesByDoc[doc.id] ?? []).filter(
                      (candidate) => candidate.status === "pending",
                    ).length === 0 && (
                      <button
                        type="button"
                        className="link smart-candidates__generate"
                        onClick={() => generateCaseCandidatesForDoc(doc.id)}
                        disabled={
                          caseCandidateBusy === `generate-case:${doc.id}`
                        }
                      >
                        {caseCandidateBusy === `generate-case:${doc.id}`
                          ? "Suche Akten …"
                          : "Aktenvorschläge erzeugen"}
                      </button>
                    )}
                </div>
              </div>
              <div className="inbox-row__side">
                <ProcessingBadge state={doc.processing_state} />
                <span className="muted">
                  {new Date(doc.added_at).toLocaleDateString("de-DE")}
                </span>
                <div className="inbox-row__actions">
                  <button type="button" onClick={() => onOpenDocument(doc.id)}>
                    Öffnen
                  </button>
                  {canEdit && (
                    <button
                      type="button"
                      onClick={() => markReviewed(doc.id)}
                      disabled={savingId === doc.id}
                    >
                      {savingId === doc.id ? "Speichere …" : "Als geprüft markieren"}
                    </button>
                  )}
                </div>
                {canEdit && (
                  <div className="inbox-learn">
                    <input
                      value={learnText}
                      onChange={(event) =>
                        setLearnTextByDoc((current) => ({
                          ...current,
                          [doc.id]: event.target.value,
                        }))
                      }
                      placeholder="Match-Text für Regel"
                    />
                    <button
                      type="button"
                      className="link"
                      onClick={() =>
                        markReviewed(doc.id, {
                          create_rule: true,
                          match_text: learnText,
                        })
                      }
                      disabled={savingId === doc.id || learnText.trim().length < 3}
                    >
                      Prüfen + Regel
                    </button>
                  </div>
                )}
              </div>
            </article>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
