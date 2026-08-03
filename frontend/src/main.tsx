import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

// Vite feuert dieses Event, wenn ein dynamischer Import (React.lazy-Chunk) nicht
// geladen werden kann – typisch nach einem Deploy, wenn der offene Tab noch die
// alten, inzwischen gelöschten Chunk-Namen referenziert. Ein frischer Seitenladen
// holt die neue index.html + Chunks. Loop-Schutz: höchstens ein Auto-Reload pro
// Zeitfenster (identisch zur ErrorBoundary), damit kein Reload-Kreislauf entsteht.
const RECOVERY_KEY = "dms_recovery_ts";
window.addEventListener("vite:preloadError", () => {
  let last = 0;
  try {
    last = Number(sessionStorage.getItem(RECOVERY_KEY) || 0);
  } catch {
    /* egal */
  }
  const now = Date.now();
  if (now - last > 30_000) {
    try {
      sessionStorage.setItem(RECOVERY_KEY, String(now));
    } catch {
      /* egal */
    }
    window.location.reload();
  }
  // Sonst greift beim tatsächlichen Render-Fehler die ErrorBoundary (Dialog).
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
