import { useState } from "react";
import {
  askDocuments,
  createDossier,
  generateDossier,
  type AskResult,
  type AskSource,
  type FolderRef,
} from "../api";
import { sanitizeSnippet } from "../sanitize";
import AgentPanel from "./AgentPanel";

export default function CopilotPage({
  folders,
  onOpenDocument,
}: {
  folders: FolderRef[];
  onOpenDocument: (documentId: number, page?: number | null) => void;
}) {
  const [question, setQuestion] = useState("");
  const [folder, setFolder] = useState<number | "none" | "">("");
  const [busy, setBusy] = useState(false);
  const [savingDossier, setSavingDossier] = useState(false);
  const [dossierNote, setDossierNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AskResult | null>(null);

  async function submit() {
    const q = question.trim();
    if (q.length < 3) return;
    setBusy(true);
    setError(null);
    setDossierNote(null);
    try {
      setResult(await askDocuments(q, folder));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Copilot-Anfrage fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }

  async function saveAsDossier() {
    const q = question.trim();
    if (!result || q.length < 3) return;
    setSavingDossier(true);
    setError(null);
    setDossierNote(null);
    try {
      const created = await createDossier({
        title: q.length > 80 ? `${q.slice(0, 77)}…` : q,
        query: q,
      });
      const generated = await generateDossier(created.id);
      setDossierNote(`Dossier „${generated.title}“ gespeichert.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Dossier konnte nicht gespeichert werden.");
    } finally {
      setSavingDossier(false);
    }
  }

  return (
    <div className="copilot">
      <section className="card copilot__panel">
        <div className="copilot__query">
          <label>
            Frage
            <textarea
              rows={3}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="z. B. Welche Versicherungen haben wir aktuell?"
            />
          </label>
          <label>
            Akte
            <select
              value={folder}
              onChange={(event) => {
                const value = event.target.value;
                setFolder(value === "" || value === "none" ? value : Number(value));
              }}
            >
              <option value="">Alle sichtbaren Dokumente</option>
              <option value="none">Ohne Ordner</option>
              {folders.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.full_path}
                </option>
              ))}
            </select>
          </label>
        </div>
        <button onClick={submit} disabled={busy || question.trim().length < 3}>
          {busy ? "Suche …" : "Fragen"}
        </button>
      </section>

      {error && <p className="status status--error">{error}</p>}

      {result && (
        <section className="copilot__answer">
          <div className="card">
            <p className="copilot__source">
              Quelle:{" "}
              {result.source === "ai"
                ? `KI (${result.provider ?? "Provider"})`
                : result.source === "unavailable"
                  ? "KI nicht verfügbar"
                  : result.source === "error"
                    ? "KI-Fehler"
                    : "Quellensuche"}
            </p>
            <p className="copilot__text">{result.answer}</p>
            <div className="copilot__answer-actions">
              <button
                onClick={saveAsDossier}
                disabled={savingDossier || result.sources.length === 0}
              >
                {savingDossier ? "Speichere …" : "Als Dossier speichern"}
              </button>
              {dossierNote && <span>{dossierNote}</span>}
            </div>
            {result.retrieval && (
              <div className="copilot__retrieval">
                <span>{result.sources.length} Quellen</span>
                <span>{result.retrieval.total_candidates} Kandidaten</span>
                {result.retrieval.query_terms.slice(0, 6).map((term) => (
                  <code key={term}>{term}</code>
                ))}
              </div>
            )}
          </div>

          <div className="copilot__sources">
            {result.sources.map((source) => (
              <SourceCard
                key={source.id}
                source={source}
                onOpen={() => onOpenDocument(source.document, source.page)}
              />
            ))}
          </div>
        </section>
      )}

      <AgentPanel />
    </div>
  );
}

function SourceCard({
  source,
  onOpen,
}: {
  source: AskSource;
  onOpen: () => void;
}) {
  return (
    <article className="card copilot-source">
      <div className="copilot-source__head">
        <strong>[{source.id}] {source.document_title}</strong>
        <button className="link" onClick={onOpen}>
          {source.page ? `Seite ${source.page} öffnen` : "Öffnen"}
        </button>
      </div>
      <p className="muted">
        {source.folder_path ?? "Kein Ordner"}
        {source.page ? ` · Seite ${source.page}` : ""}
        {source.source_type ? ` · ${sourceTypeLabel(source.source_type)}` : ""}
      </p>
      {(source.reason || source.case_file || source.contract) && (
        <div className="copilot-source__context">
          {source.reason && <span>{source.reason}</span>}
          {source.case_file && (
            <span>
              Akte: <strong>{source.case_file.title}</strong>
            </span>
          )}
          {source.contract && (
            <span>
              Vertrag:{" "}
              <strong>
                {source.contract.provider || source.contract.contract_type_label}
              </strong>
              {source.contract.contract_number ? ` · ${source.contract.contract_number}` : ""}
            </span>
          )}
        </div>
      )}
      {!!source.entities?.length && (
        <div className="copilot-source__entities">
          {source.entities.slice(0, 5).map((entity) => (
            <span key={`${entity.id}-${entity.role}`}>
              {entity.name}
            </span>
          ))}
        </div>
      )}
      <p
        className="copilot-source__snippet"
        dangerouslySetInnerHTML={{
          __html: sanitizeSnippet(source.snippet_html || source.snippet),
        }}
      />
    </article>
  );
}

function sourceTypeLabel(type: NonNullable<AskSource["source_type"]>) {
  if (type === "semantic") return "Semantik";
  if (type === "page_text") return "Seitentext";
  if (type === "ocr_text") return "OCR";
  return "Metadaten";
}
