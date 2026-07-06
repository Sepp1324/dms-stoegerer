import { useEffect, useState } from "react";
import { getBackupStatus, type BackupMonitorEntry, type BackupStatus } from "../api";

function formatDate(value: string | null): string {
  if (!value) return "Nie";
  return new Intl.DateTimeFormat("de-AT", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function statusLabel(status: BackupMonitorEntry["status"]): string {
  switch (status) {
    case "success":
      return "Erfolgreich";
    case "running":
      return "Läuft";
    case "failed":
      return "Fehlgeschlagen";
    default:
      return "Unbekannt";
  }
}

function toneFor(entry: BackupMonitorEntry): "ok" | "warn" | "error" {
  if (entry.status === "failed") return "error";
  if (entry.stale || entry.status === "unknown" || entry.status === "running") return "warn";
  return "ok";
}

function ageLabel(hours: number | null): string {
  if (hours === null) return "Keine erfolgreiche Ausführung";
  if (hours < 1) return "vor weniger als 1 Stunde";
  if (hours < 48) return `vor ${Math.round(hours)} Stunden`;
  return `vor ${Math.round(hours / 24)} Tagen`;
}

function StatusCard({
  title,
  entry,
  extra,
}: {
  title: string;
  entry: BackupMonitorEntry;
  extra?: string;
}) {
  const tone = toneFor(entry);
  return (
    <article className={`system-card system-card--${tone}`}>
      <div className="system-card__head">
        <h3>{title}</h3>
        <span className={`system-pill system-pill--${tone}`}>{statusLabel(entry.status)}</span>
      </div>
      <dl className="system-card__grid">
        <div>
          <dt>Letzter Erfolg</dt>
          <dd>{formatDate(entry.last_success_at)}</dd>
        </div>
        <div>
          <dt>Alter</dt>
          <dd>{ageLabel(entry.age_hours)}</dd>
        </div>
        <div>
          <dt>Artefakt</dt>
          <dd>{entry.artifact_timestamp || "-"}</dd>
        </div>
        <div>
          <dt>Zuletzt aktualisiert</dt>
          <dd>{formatDate(entry.updated_at)}</dd>
        </div>
      </dl>
      {entry.message && <p className="system-card__message">{entry.message}</p>}
      {extra && <p className="system-card__message">{extra}</p>}
    </article>
  );
}

export default function SystemStatusPage() {
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    getBackupStatus()
      .then(setStatus)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  if (loading) return <p className="muted" role="status">Lade Systemstatus …</p>;
  if (error) {
    return (
      <section className="system-status">
        <p className="status status--error">{error}</p>
        <button onClick={load}>Erneut laden</button>
      </section>
    );
  }
  if (!status) return null;

  const tone = status.status;
  const headline =
    tone === "ok"
      ? "Backup-System läuft"
      : tone === "warn"
        ? "Backup-System braucht Aufmerksamkeit"
        : "Backup-System hat einen Fehler";

  return (
    <section className="system-status">
      <div className={`system-banner system-banner--${tone}`}>
        <div>
          <h2>{headline}</h2>
          <p>
            Letztes Backup wird nach {status.cronjob.alert_after_hours} Stunden ohne
            erfolgreichen Lauf als veraltet markiert.
          </p>
        </div>
        <button onClick={load}>Aktualisieren</button>
      </div>

      <div className="system-grid">
        <StatusCard
          title="Backup-CronJob"
          entry={status.backup}
          extra={`CronJob ${status.cronjob.name}: ${status.cronjob.schedule}, erwartet alle ${status.cronjob.expected_interval_hours}h.`}
        />
        <StatusCard title="Restore-Drill" entry={status.restore_drill} />
      </div>
    </section>
  );
}
