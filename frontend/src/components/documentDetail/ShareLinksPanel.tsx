import { useEffect, useState } from "react";
import {
  createShareLink,
  getShareLinks,
  revokeShareLink,
  type ShareLink,
} from "../../api";
import { formatDateTime } from "./format";

// Ablauf-Schnellwahl (Tage) für neue Freigabelinks. Kein „nie" – Ablauf ist
// Pflicht (STOAA-192). Default 30 ist der mittlere, vorbelegte Wert.
const SHARE_EXPIRY_CHOICES = [7, 30, 90] as const;
const SHARE_EXPIRY_DEFAULT = 30;

// Leitet den Anzeige-Status eines Links ab. is_valid (Backend) = weder
// widerrufen noch abgelaufen; hier zusätzlich widerrufen ↔ abgelaufen getrennt.
type ShareLinkState = "gueltig" | "abgelaufen" | "widerrufen";
function shareLinkState(link: ShareLink): ShareLinkState {
  if (link.revoked_at) return "widerrufen";
  if (new Date(link.expires_at).getTime() <= Date.now()) return "abgelaufen";
  return "gueltig";
}
const SHARE_STATE_LABELS: Record<ShareLinkState, string> = {
  gueltig: "gültig",
  abgelaufen: "abgelaufen",
  widerrufen: "widerrufen",
};

// Freigabelinks-Sektion (STOAA-192): „Link teilen"-Dialog mit Pflicht-Ablauf
// (7/30/90 Tage, Default 30) + Link-Verwaltung je Dokument (Ablauf, Status,
// Widerruf). Nur bei Schreibrecht sichtbar – Gäste sehen die Sektion nicht.
// Der Klartext-Token kommt einmalig aus der Create-Response und wird direkt
// angezeigt + in die Zwischenablage kopiert. Die Aufruf-Seite /share/<token>
// ist NICHT Teil dieses Tickets (→ Ticket D).
export function ShareLinksPanel({
  documentId,
  canEdit,
}: {
  documentId: number;
  canEdit: boolean;
}) {
  const [links, setLinks] = useState<ShareLink[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [days, setDays] = useState<number>(SHARE_EXPIRY_DEFAULT);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  // Nach erfolgreichem Create: Klartext-Link (einmalig) + Kopier-Status.
  const [created, setCreated] = useState<{ url: string; copied: boolean } | null>(
    null,
  );
  const [revokingId, setRevokingId] = useState<number | null>(null);

  // Nur bei Schreibrecht laden/anzeigen – Gäste haben ohnehin keinen Zugriff.
  useEffect(() => {
    if (!canEdit) return;
    let active = true;
    setLinks(null);
    setLoadError(null);
    getShareLinks(documentId)
      .then((rows) => active && setLinks(rows))
      .catch((e) => active && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => {
      active = false;
    };
  }, [documentId, canEdit]);

  if (!canEdit) return null;

  function openDialog() {
    setDays(SHARE_EXPIRY_DEFAULT);
    setCreateError(null);
    setCreated(null);
    setDialogOpen(true);
  }

  async function submit() {
    setCreating(true);
    setCreateError(null);
    try {
      // Ablauf = jetzt + gewählte Tage (immer in der Zukunft → Backend-konform).
      const expiresAt = new Date(Date.now() + days * 86400000).toISOString();
      const link = await createShareLink(documentId, expiresAt);
      const url = `${window.location.origin}/share/${link.token}`;
      let copied = false;
      try {
        await navigator.clipboard.writeText(url);
        copied = true;
      } catch {
        // Zwischenablage evtl. gesperrt (kein HTTPS/Fokus) – Link bleibt sichtbar.
      }
      setCreated({ url, copied });
      // Liste aktualisieren (neuer Link erscheint, ohne Klartext-Token).
      setLinks((prev) => (prev ? [link, ...prev] : [link]));
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  async function copyAgain() {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.url);
      setCreated({ ...created, copied: true });
    } catch {
      /* Zwischenablage nicht verfügbar */
    }
  }

  async function revoke(id: number) {
    setRevokingId(id);
    try {
      const updated = await revokeShareLink(id);
      setLinks((prev) => prev?.map((l) => (l.id === id ? updated : l)) ?? null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <div className="share-links">
      <div className="share-links__head">
        <h3>Freigabelinks</h3>
        <button className="link" onClick={openDialog}>
          + Link teilen
        </button>
      </div>

      {loadError && <p className="status status--error">{loadError}</p>}
      {links === null && !loadError && <p className="muted">Lade …</p>}
      {links && links.length === 0 && (
        <p className="muted share-links__empty">Noch keine Freigabelinks.</p>
      )}
      {links && links.length > 0 && (
        <ul className="share-links__list">
          {links.map((link) => {
            const state = shareLinkState(link);
            return (
              <li key={link.id} className="share-links__row">
                <span className={`share-badge share-badge--${state}`}>
                  {SHARE_STATE_LABELS[state]}
                </span>
                <span className="share-links__expiry">
                  Ablauf: {formatDateTime(link.expires_at)}
                </span>
                {state === "gueltig" ? (
                  <button
                    className="share-links__revoke"
                    onClick={() => revoke(link.id)}
                    disabled={revokingId === link.id}
                  >
                    {revokingId === link.id ? "…" : "Widerrufen"}
                  </button>
                ) : (
                  <span />
                )}
              </li>
            );
          })}
        </ul>
      )}

      {dialogOpen && (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Link teilen"
          onClick={() => !creating && setDialogOpen(false)}
        >
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-card__head">
              <h3>Link teilen</h3>
              <button
                className="link"
                onClick={() => setDialogOpen(false)}
                disabled={creating}
                aria-label="Schließen"
              >
                ✕
              </button>
            </div>

            {!created ? (
              <>
                <p className="muted share-dialog__hint">
                  Der Link läuft automatisch ab – ein Ablaufdatum ist Pflicht.
                </p>
                <fieldset className="share-dialog__choices">
                  <legend className="share-dialog__legend">Gültig für</legend>
                  {SHARE_EXPIRY_CHOICES.map((choice) => (
                    <label
                      key={choice}
                      className={`share-choice ${days === choice ? "share-choice--on" : ""}`}
                    >
                      <input
                        type="radio"
                        name="share-expiry"
                        value={choice}
                        checked={days === choice}
                        onChange={() => setDays(choice)}
                      />
                      {choice} Tage
                    </label>
                  ))}
                </fieldset>
                {createError && <p className="status status--error">{createError}</p>}
                <div className="modal-card__actions">
                  <button onClick={submit} disabled={creating}>
                    {creating ? "Erstelle …" : "Link erstellen"}
                  </button>
                  <button
                    className="link"
                    onClick={() => setDialogOpen(false)}
                    disabled={creating}
                  >
                    Abbrechen
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="share-dialog__ok">
                  {created.copied
                    ? "Link erstellt und in die Zwischenablage kopiert."
                    : "Link erstellt. Bitte manuell kopieren:"}
                </p>
                <div className="share-dialog__link">
                  <input readOnly value={created.url} onFocus={(e) => e.target.select()} />
                  <button onClick={copyAgain}>Kopieren</button>
                </div>
                <p className="muted share-dialog__hint">
                  Dieser Link wird nur einmalig angezeigt und lässt sich später
                  nicht erneut abrufen.
                </p>
                <div className="modal-card__actions">
                  <button className="link" onClick={() => setDialogOpen(false)}>
                    Schließen
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
