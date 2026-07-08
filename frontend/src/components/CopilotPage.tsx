import { useState } from "react";
import {
  askDocuments,
  type AskResult,
  type AskSource,
  type FolderRef,
} from "../api";
import { sanitizeSnippet } from "../sanitize";

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
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AskResult | null>(null);

  async function submit() {
    const q = question.trim();
    if (q.length < 3) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await askDocuments(q, folder));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Copilot-Anfrage fehlgeschlagen.");
    } finally {
      setBusy(false);
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
      </p>
      <p
        className="copilot-source__snippet"
        dangerouslySetInnerHTML={{
          __html: sanitizeSnippet(source.snippet_html || source.snippet),
        }}
      />
    </article>
  );
}
