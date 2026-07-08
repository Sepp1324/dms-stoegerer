import { useEffect, useMemo, useState } from "react";
import {
  addDocumentsToCaseFile,
  createCaseFile,
  getCaseFiles,
  getDocuments,
  removeDocumentsFromCaseFile,
  summarizeCaseFile,
  updateCaseFile,
  type AskSource,
  type CaseFile,
  type CaseFileStatus,
  type DocumentItem,
} from "../api";
import { sanitizeSnippet } from "../sanitize";

const STATUS_OPTIONS: { value: CaseFileStatus; label: string }[] = [
  { value: "active", label: "Aktiv" },
  { value: "waiting", label: "Wartet" },
  { value: "done", label: "Erledigt" },
  { value: "archived", label: "Archiviert" },
];

export default function CaseFilesPage({
  canEdit,
  onOpenDocument,
}: {
  canEdit: boolean;
  onOpenDocument: (documentId: number, page?: number | null) => void;
}) {
  const [caseFiles, setCaseFiles] = useState<CaseFile[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [addDocumentId, setAddDocumentId] = useState("");
  const [sourcesByCase, setSourcesByCase] = useState<Record<number, AskSource[]>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    Promise.all([
      getCaseFiles(),
      getDocuments({ ordering: "-added_at" }),
    ])
      .then(([cases, docs]) => {
        setCaseFiles(cases);
        setDocuments(docs.results);
        setSelectedId((current) => current ?? cases[0]?.id ?? null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  const selected = useMemo(
    () => caseFiles.find((item) => item.id === selectedId) ?? null,
    [caseFiles, selectedId],
  );

  const availableDocuments = useMemo(() => {
    if (!selected) return documents;
    const selectedDocIds = new Set(selected.documents.map((doc) => doc.id));
    return documents.filter((doc) => !selectedDocIds.has(doc.id));
  }, [documents, selected]);

  function replaceCaseFile(updated: CaseFile) {
    setCaseFiles((current) =>
      current.map((item) => (item.id === updated.id ? updated : item)),
    );
  }

  async function createNewCase() {
    const cleanTitle = title.trim();
    if (!cleanTitle) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createCaseFile({
        title: cleanTitle,
        description: description.trim(),
      });
      setCaseFiles((current) => [created, ...current]);
      setSelectedId(created.id);
      setTitle("");
      setDescription("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function changeStatus(value: CaseFileStatus) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      replaceCaseFile(await updateCaseFile(selected.id, { status: value }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function addDocument() {
    if (!selected || !addDocumentId) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await addDocumentsToCaseFile(selected.id, [Number(addDocumentId)]);
      replaceCaseFile(updated);
      setDocuments((current) =>
        current.map((doc) =>
          doc.id === Number(addDocumentId)
            ? { ...doc, case_file: updated.id, case_file_title: updated.title }
            : doc,
        ),
      );
      setAddDocumentId("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function removeDocument(documentId: number) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await removeDocumentsFromCaseFile(selected.id, [documentId]);
      replaceCaseFile(updated);
      setDocuments((current) =>
        current.map((doc) =>
          doc.id === documentId
            ? { ...doc, case_file: null, case_file_title: null }
            : doc,
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function summarize() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const result = await summarizeCaseFile(selected.id);
      replaceCaseFile(result.case_file);
      setSourcesByCase((current) => ({
        ...current,
        [selected.id]: result.sources,
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <section className="case-files">
        <div className="state">
          <strong>Akten werden geladen.</strong>
          <span>Dokumente und Vorgänge werden zusammengeführt.</span>
        </div>
      </section>
    );
  }

  return (
    <section className="case-files">
      {error && (
        <div className="state state--error">
          <strong>Akten konnten nicht aktualisiert werden.</strong>
          <span>{error}</span>
        </div>
      )}

      <div className="case-files__layout">
        <aside className="case-files__list">
          <div className="case-files__head">
            <div>
              <p className="eyebrow">Vorgänge</p>
              <h2>{caseFiles.length} Akten</h2>
            </div>
            <button className="link" onClick={load}>
              Aktualisieren
            </button>
          </div>

          {canEdit && (
            <div className="case-create">
              <input
                placeholder="Neue Akte"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
              <textarea
                rows={2}
                placeholder="Kurzbeschreibung"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
              <button onClick={createNewCase} disabled={busy || !title.trim()}>
                Anlegen
              </button>
            </div>
          )}

          <div className="case-list">
            {caseFiles.length === 0 ? (
              <p className="muted">Noch keine Akten angelegt.</p>
            ) : (
              caseFiles.map((item) => (
                <button
                  key={item.id}
                  className={`case-list__item${
                    selected?.id === item.id ? " case-list__item--active" : ""
                  }`}
                  onClick={() => setSelectedId(item.id)}
                >
                  <strong>{item.title}</strong>
                  <span>
                    {item.status_label} · {item.document_count} Dokument
                    {item.document_count === 1 ? "" : "e"}
                  </span>
                </button>
              ))
            )}
          </div>
        </aside>

        <main className="case-detail">
          {!selected ? (
            <div className="state">
              <strong>Keine Akte ausgewählt.</strong>
              <span>Lege links eine neue Akte an oder wähle einen Vorgang.</span>
            </div>
          ) : (
            <>
              <div className="case-detail__top">
                <div>
                  <p className="eyebrow">Akte</p>
                  <h2>{selected.title}</h2>
                  {selected.description && <p>{selected.description}</p>}
                </div>
                <label>
                  Status
                  <select
                    value={selected.status}
                    onChange={(event) =>
                      changeStatus(event.target.value as CaseFileStatus)
                    }
                    disabled={!canEdit || busy}
                  >
                    {STATUS_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <section className="case-summary">
                <div className="case-section-head">
                  <h3>Akten-Gedächtnis</h3>
                  {canEdit && (
                    <button onClick={summarize} disabled={busy}>
                      {busy ? "Arbeite …" : "Zusammenfassen"}
                    </button>
                  )}
                </div>
                {selected.ai_summary ? (
                  <>
                    <p className="case-summary__text">{selected.ai_summary}</p>
                    <p className="muted">
                      Quelle: {selected.ai_summary_source || "unbekannt"}
                      {selected.ai_summary_generated_at
                        ? ` · ${new Date(
                            selected.ai_summary_generated_at,
                          ).toLocaleString("de-DE")}`
                        : ""}
                    </p>
                  </>
                ) : (
                  <p className="muted">
                    Noch keine Zusammenfassung. Starte sie, sobald Dokumente in der
                    Akte liegen.
                  </p>
                )}
                {(sourcesByCase[selected.id] ?? []).length > 0 && (
                  <div className="case-sources">
                    {(sourcesByCase[selected.id] ?? []).map((source) => (
                      <article className="case-source" key={source.id}>
                        <div className="case-source__head">
                          <strong>[{source.id}] {source.document_title}</strong>
                          <button
                            className="link"
                            onClick={() => onOpenDocument(source.document, source.page)}
                          >
                            {source.page ? `Seite ${source.page}` : "Öffnen"}
                          </button>
                        </div>
                        <p
                          dangerouslySetInnerHTML={{
                            __html: sanitizeSnippet(
                              source.snippet_html || source.snippet,
                            ),
                          }}
                        />
                      </article>
                    ))}
                  </div>
                )}
              </section>

              <section>
                <div className="case-section-head">
                  <h3>Timeline</h3>
                  {canEdit && (
                    <div className="case-add-doc">
                      <select
                        value={addDocumentId}
                        onChange={(event) => setAddDocumentId(event.target.value)}
                        disabled={busy}
                      >
                        <option value="">Dokument hinzufügen …</option>
                        {availableDocuments.map((doc) => (
                          <option key={doc.id} value={doc.id}>
                            {doc.title}
                          </option>
                        ))}
                      </select>
                      <button onClick={addDocument} disabled={busy || !addDocumentId}>
                        Hinzufügen
                      </button>
                    </div>
                  )}
                </div>

                {selected.documents.length === 0 ? (
                  <div className="state">
                    <strong>Noch keine Dokumente.</strong>
                    <span>Füge Dokumente hinzu, damit daraus ein Vorgang wird.</span>
                  </div>
                ) : (
                  <div className="case-timeline">
                    {selected.documents.map((doc) => (
                      <article className="case-timeline__item" key={doc.id}>
                        <div className="case-timeline__dot" />
                        <div className="case-timeline__body">
                          <button
                            type="button"
                            className="case-timeline__title"
                            onClick={() => onOpenDocument(doc.id)}
                          >
                            {doc.title}
                          </button>
                          <p className="muted">
                            {doc.correspondent_name ?? "Unbekannt"}
                            {doc.document_type_name
                              ? ` · ${doc.document_type_name}`
                              : ""}
                            {doc.folder_path ? ` · ${doc.folder_path}` : ""}
                          </p>
                          <p className="muted">
                            {new Date(doc.added_at).toLocaleDateString("de-DE")}
                            {doc.asn_label ? ` · ${doc.asn_label}` : ""}
                            {doc.page_count != null
                              ? ` · ${doc.page_count} Seite${
                                  doc.page_count === 1 ? "" : "n"
                                }`
                              : ""}
                          </p>
                          {canEdit && (
                            <button
                              className="link"
                              onClick={() => removeDocument(doc.id)}
                              disabled={busy}
                            >
                              Aus Akte entfernen
                            </button>
                          )}
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </>
          )}
        </main>
      </div>
    </section>
  );
}
