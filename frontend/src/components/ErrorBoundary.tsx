import { Component, type ErrorInfo, type ReactNode } from "react";

import { logout } from "../api";

// Selbstheilende ErrorBoundary (Bug: „ab und zu lädt die Seite nicht mehr").
// Ursache ist meist ein fehlgeschlagener dynamischer Import (React.lazy): Nach
// einem Deploy bekommen die JS-Chunks neue Hash-Namen; ein offener Tab / ein noch
// nicht aktualisierter Service-Worker referenziert die ALTEN, inzwischen
// gelöschten Chunks -> der Import wirft -> ohne Boundary bleibt die Seite weiß.
//
// Verhalten: Beim ersten Fehler wird EINMAL automatisch neu geladen (holt frische
// index.html + Chunks; der Nutzer muss sich NICHT manuell ausloggen). Tritt
// derselbe Fehler kurz darauf erneut auf (Loop-Schutz über einen Zeitstempel),
// zeigen wir einen klaren Wiederherstellungs-Dialog mit „Neu laden" und
// „Abmelden & neu laden".

const RECOVERY_KEY = "dms_recovery_ts";
const RECOVERY_WINDOW_MS = 30_000;

function isChunkLoadError(error: unknown): boolean {
  const msg = error instanceof Error ? `${error.name} ${error.message}` : String(error);
  return /ChunkLoadError|dynamically imported module|Importing a module script failed|Loading chunk|Failed to fetch/i.test(
    msg,
  );
}

interface State {
  failed: boolean;
}

export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Fürs Debugging protokollieren (nicht an den Nutzer weiterreichen).
    console.error("ErrorBoundary hat einen Fehler abgefangen:", error, info);

    // Loop-Schutz: höchstens EIN automatischer Reload innerhalb des Zeitfensters.
    let last = 0;
    try {
      last = Number(sessionStorage.getItem(RECOVERY_KEY) || 0);
    } catch {
      /* sessionStorage evtl. nicht verfügbar */
    }
    const now = Date.now();
    if (now - last > RECOVERY_WINDOW_MS) {
      try {
        sessionStorage.setItem(RECOVERY_KEY, String(now));
      } catch {
        /* egal */
      }
      // Chunk-Fehler heilen zuverlässig durch einen frischen Seitenladen.
      window.location.reload();
      return;
    }
    // Kürzlich schon neu geladen und es kracht weiter -> manuellen Dialog zeigen
    // (kein Endlos-Reload). Der Fehlertyp entscheidet die Empfehlung.
    void isChunkLoadError(error);
  }

  private hardReload = () => {
    try {
      sessionStorage.removeItem(RECOVERY_KEY);
    } catch {
      /* egal */
    }
    window.location.reload();
  };

  private logoutAndReload = () => {
    try {
      sessionStorage.removeItem(RECOVERY_KEY);
    } catch {
      /* egal */
    }
    logout(); // lokalen Auth-State löschen (+ best-effort Server-Logout)
    window.location.assign("/");
  };

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="error-recovery" role="alert">
        <h1>Die Seite konnte nicht geladen werden</h1>
        <p>
          Das passiert meist direkt nach einem Update. Ein Neuladen behebt es in
          der Regel.
        </p>
        <div className="error-recovery__actions">
          <button onClick={this.hardReload}>Neu laden</button>
          <button className="link" onClick={this.logoutAndReload}>
            Abmelden &amp; neu laden
          </button>
        </div>
      </div>
    );
  }
}
