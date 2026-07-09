import { useEffect, useMemo, useState } from "react";
import {
  createDossier,
  exportDossierMarkdown,
  finalizeDossier,
  generateDossier,
  getDossiers,
  type AskSource,
  type Dossier,
} from "../api";
import { sanitizeSnippet } from "../sanitize";

export default function DossiersPage({
  canEdit,
  onOpenDocument,
}: {
  canEdit: boolean;
  onOpenDocument: (documentId: number, page?: number | null) => void;
}) {
  const [dossiers, setDossiers] = useState<Dossier[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    getDossiers()
      .then((items) => {
        setDossiers(items);
        setSelectedId((current) => current ?? items[0]?.id ?? null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  const selected = useMemo(
    () => dossiers.find((item) => item.id === selectedId) ?? null,
    [dossiers, selectedId],
  );

  function replace(updated: Dossier) {
    setDossiers((current) =>
      current.map((item) => (item.id === updated.id ? updated : item)),
    );
  }

  async function createNew() {
    const cleanTitle = title.trim();
    const cleanQuery = query.trim();
    if (!cleanTitle || cleanQuery.length < 3) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createDossier({ title: cleanTitle, query: cleanQuery });
      setDossiers((current) => [created, ...current]);
      setSelectedId(created.id);
      setTitle("");
      setQuery("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function generateSelected() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      replace(await generateDossier(selected.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function finalizeSelected() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      replace(await finalizeDossier(selected.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function exportMarkdown() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const blob = await exportDossierMarkdown(selected.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${safeFilename(selected.title || "dossier")}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <section className="dossiers">
        <div className="state">
          <strong>Dossiers werden geladen.</strong>
          <span>Gespeicherte Rechercheakten werden vorbereitet.</span>
        </div>
      </section>
    );
  }

  return (
    <section className="dossiers">
      {error && (
        <div className="state state--error">
          <strong>Dossier konnte nicht aktualisiert werden.</strong>
          <span>{error}</span>
        </div>
      )}

      <div className="dossiers__layout">
        <aside className="dossiers__list">
          <div className="dossiers__head">
            <div>
              <p className="eyebrow">Beweisakten</p>
              <h2>{dossiers.length} Dossiers</h2>
            </div>
            <button className="link" onClick={load}>
              Aktualisieren
            </button>
          </div>

          {canEdit && (
            <div className="dossier-create">
              <input
                placeholder="Titel, z. B. Helvetia Unterlagen"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
              <textarea
                rows={3}
                placeholder="Frage/Thema, z. B. Alles zur Helvetia-Polizze von Cornelia"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <button
                onClick={createNew}
                disabled={busy || !title.trim() || query.trim().length < 3}
              >
                Anlegen
              </button>
            </div>
          )}

          <div className="dossier-list">
            {dossiers.length === 0 ? (
              <p className="muted">Noch keine Dossiers gespeichert.</p>
            ) : (
              dossiers.map((item) => (
                <button
                  key={item.id}
                  className={`dossier-list__item${
                    selected?.id === item.id ? " dossier-list__item--active" : ""
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

        <main className="dossier-detail">
          {!selected ? (
            <div className="state">
              <strong>Kein Dossier ausgewählt.</strong>
              <span>Lege links eine neue Beweisakte an.</span>
            </div>
          ) : (
            <>
              <div className="dossier-detail__top">
                <div>
                  <p className="eyebrow">Dossier</p>
                  <h2>{selected.title}</h2>
                  <p>{selected.query}</p>
                  <p className="muted">
                    Status: {selected.status_label} · Quelle:{" "}
                    {selected.generated_source_label}
                    {selected.generated_at
                      ? ` · ${new Date(selected.generated_at).toLocaleString("de-DE")}`
                      : ""}
                  </p>
                </div>
                <div className="dossier-actions">
                  {canEdit && selected.status !== "final" && (
                    <button onClick={generateSelected} disabled={busy}>
                      {busy ? "Arbeite …" : selected.summary ? "Neu generieren" : "Generieren"}
                    </button>
                  )}
                  {canEdit && selected.status === "generated" && (
                    <button onClick={finalizeSelected} disabled={busy}>
                      Finalisieren
                    </button>
                  )}
                  <button onClick={exportMarkdown} disabled={busy}>
                    Markdown
                  </button>
                </div>
              </div>

              <section className="dossier-summary">
                <h3>Kurzfassung</h3>
                {selected.summary ? (
                  <p>{selected.summary}</p>
                ) : (
                  <p className="muted">
                    Noch nicht generiert. Starte die Generierung, sobald die Frage passt.
                  </p>
                )}
              </section>

              <section className="dossier-grid">
                <DossierTimeline items={selected.timeline} onOpenDocument={onOpenDocument} />
                <DossierEntities entities={selected.entities} />
                <DossierContracts contracts={selected.contracts} />
              </section>

              <section className="dossier-sources">
                <h3>Quellen</h3>
                {selected.sources.length === 0 ? (
                  <p className="muted">Noch keine Quellen gespeichert.</p>
                ) : (
                  <div className="case-sources">
                    {selected.sources.map((source) => (
                      <SourceCard
                        key={source.id}
                        source={source}
                        onOpen={() => onOpenDocument(source.document, source.page)}
                      />
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

function safeFilename(value: string) {
  return value.trim().replace(/[^a-z0-9äöüß._-]+/gi, "-").replace(/^-+|-+$/g, "") || "dossier";
}

function DossierTimeline({
  items,
  onOpenDocument,
}: {
  items: Dossier["timeline"];
  onOpenDocument: (documentId: number, page?: number | null) => void;
}) {
  return (
    <section className="dossier-widget">
      <h3>Timeline</h3>
      {items.length === 0 ? (
        <p className="muted">Keine Timeline-Einträge.</p>
      ) : (
        items.map((item) => (
          <button
            key={item.document}
            className="dossier-timeline-item"
            onClick={() => onOpenDocument(item.document, item.page)}
          >
            <strong>{item.title}</strong>
            <span>{item.sources.join(", ")}</span>
          </button>
        ))
      )}
    </section>
  );
}

function DossierEntities({ entities }: { entities: Dossier["entities"] }) {
  return (
    <section className="dossier-widget">
      <h3>Entitäten</h3>
      {entities.length === 0 ? (
        <p className="muted">Keine Entitäten erkannt.</p>
      ) : (
        <div className="dossier-chips">
          {entities.slice(0, 12).map((entity) => (
            <span key={`${entity.id}-${entity.name}`}>
              {entity.name}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function DossierContracts({ contracts }: { contracts: Dossier["contracts"] }) {
  return (
    <section className="dossier-widget">
      <h3>Verträge</h3>
      {contracts.length === 0 ? (
        <p className="muted">Keine Vertragsdaten erkannt.</p>
      ) : (
        contracts.map((contract) => (
          <div className="dossier-contract" key={`${contract.id}-${contract.provider}`}>
            <strong>{contract.provider || contract.contract_type_label || "Vertrag"}</strong>
            <span>
              {contract.contract_number || "ohne Nummer"} · {contract.status_label || "-"}
            </span>
          </div>
        ))
      )}
    </section>
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
    <article className="case-source">
      <div className="case-source__head">
        <strong>[{source.id}] {source.document_title}</strong>
        <button className="link" onClick={onOpen}>
          {source.page ? `Seite ${source.page}` : "Öffnen"}
        </button>
      </div>
      <p className="muted">
        {source.folder_path ?? "Kein Ordner"}
        {source.reason ? ` · ${source.reason}` : ""}
      </p>
      <p
        dangerouslySetInnerHTML={{
          __html: sanitizeSnippet(source.snippet_html || source.snippet),
        }}
      />
    </article>
  );
}
