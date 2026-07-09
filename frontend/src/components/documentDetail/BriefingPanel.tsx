import { useEffect, useState, type ReactNode } from "react";
import {
  getDocumentBriefing,
  type DocumentBriefing,
  type DocumentBriefingAction,
} from "../../api";
import type { TabId } from "./DetailTabs";
import { formatIsoDate } from "./format";

export function BriefingPanel({
  documentId,
  canEdit,
  onSelectTab,
  onOpenDocument,
}: {
  documentId: number;
  canEdit: boolean;
  onSelectTab: (tab: TabId) => void;
  onOpenDocument: (documentId: number) => void;
}) {
  const [briefing, setBriefing] = useState<DocumentBriefing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setBriefing(await getDocumentBriefing(documentId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    getDocumentBriefing(documentId)
      .then((data) => active && setBriefing(data))
      .catch((err) => active && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [documentId]);

  if (loading) return <p className="muted">Briefing wird erstellt …</p>;
  if (error) return <p className="status status--error">{error}</p>;
  if (!briefing) return <p className="muted">Kein Briefing verfügbar.</p>;

  return (
    <div className="briefing">
      <header className={`briefing-hero briefing-hero--${briefing.risk_level}`}>
        <div>
          <span className="briefing-eyebrow">DMS-Copilot</span>
          <h3>{riskLabel(briefing.risk_level)}</h3>
          <p>{briefing.summary.text}</p>
        </div>
        <button type="button" className="link" onClick={load}>
          Aktualisieren
        </button>
      </header>

      <section className="briefing-metrics" aria-label="Briefing Kennzahlen">
        <Metric label="Metadaten" value={`${briefing.metadata_score.percent}%`} />
        <Metric label="OCR-Wörter" value={briefing.signals.ocr.words.toLocaleString("de-DE")} />
        <Metric label="Aufgaben" value={briefing.next_actions.length.toString()} />
        <Metric
          label="Archiv"
          value={briefing.health.archive_status_label}
          tone={briefing.health.archive_status === "error" ? "danger" : undefined}
        />
      </section>

      <section className="briefing-grid">
        <Panel title="Nächste Aktionen">
          {briefing.next_actions.length ? (
            <div className="briefing-actions">
              {briefing.next_actions.map((action) => (
                <ActionRow
                  key={`${action.kind}-${action.priority}`}
                  action={action}
                  canEdit={canEdit}
                  onSelectTab={onSelectTab}
                />
              ))}
            </div>
          ) : (
            <p className="muted">Keine akuten nächsten Schritte.</p>
          )}
        </Panel>

        <Panel title="Risiken">
          {briefing.risks.length ? (
            <div className="briefing-risk-list">
              {briefing.risks.map((risk) => (
                <div className={`briefing-risk briefing-risk--${risk.level}`} key={risk.label}>
                  <strong>{risk.label}</strong>
                  <span>{risk.detail}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">Keine markanten Risiken erkannt.</p>
          )}
        </Panel>

        <Panel title="Signale">
          <div className="briefing-signal-list">
            <Signal label="Verarbeitung" value={stateLabel(briefing.health.processing_state)} />
            <Signal label="OCR" value={briefing.health.ocr_status ?? "unbekannt"} />
            <Signal label="Siegel" value={briefing.health.sealed ? "vorhanden" : "fehlt"} />
            <Signal label="Legal Hold" value={briefing.health.legal_hold ? "aktiv" : "nein"} />
            {briefing.signals.contract && (
              <Signal
                label="Vertrag"
                value={`${briefing.signals.contract.provider_display || "Unbekannt"} · ${
                  briefing.signals.contract.status_label
                }`}
              />
            )}
          </div>
        </Panel>

        <Panel title="Beziehungen">
          {briefing.relations.entities.length ? (
            <div className="briefing-chip-list">
              {briefing.relations.entities.map((entity) => (
                <span key={`${entity.id}-${entity.role}`} className="briefing-chip">
                  {entity.name}
                  <small>{entity.role_label}</small>
                </span>
              ))}
            </div>
          ) : (
            <p className="muted">Keine Entitäten verknüpft.</p>
          )}
          {briefing.relations.related_documents.length > 0 && (
            <div className="briefing-related">
              {briefing.relations.related_documents.map((doc) => (
                <button
                  type="button"
                  className="link"
                  key={doc.id}
                  onClick={() => onOpenDocument(doc.id)}
                >
                  {doc.title} · {doc.reason}
                </button>
              ))}
            </div>
          )}
        </Panel>
      </section>

      <section className="briefing-timeline">
        <h3>Verlauf</h3>
        <div>
          {briefing.timeline.map((item) => (
            <span key={`${item.kind}-${item.date}-${item.label}`}>
              <strong>{item.label}</strong>
              {item.date ? formatIsoDate(item.date) : "ohne Datum"}
            </span>
          ))}
        </div>
      </section>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "danger";
}) {
  return (
    <div className={`briefing-metric ${tone ? `briefing-metric--${tone}` : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="briefing-panel">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function ActionRow({
  action,
  canEdit,
  onSelectTab,
}: {
  action: DocumentBriefingAction;
  canEdit: boolean;
  onSelectTab: (tab: TabId) => void;
}) {
  const canNavigate = action.target !== "ai" || canEdit;
  return (
    <article className="briefing-action">
      <div>
        <strong>{action.title}</strong>
        <span>{action.description}</span>
      </div>
      {canNavigate && (
        <button type="button" className="link" onClick={() => onSelectTab(action.target)}>
          {action.action_label}
        </button>
      )}
    </article>
  );
}

function Signal({ label, value }: { label: string; value: string }) {
  return (
    <div className="briefing-signal">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function riskLabel(level: DocumentBriefing["risk_level"]) {
  if (level === "high") return "Sofort prüfen";
  if (level === "medium") return "Aufmerksamkeit nötig";
  if (level === "low") return "Kleine offene Punkte";
  return "Alles ruhig";
}

function stateLabel(value: string | null) {
  if (!value) return "unbekannt";
  return value.replace(/_/g, " ");
}
