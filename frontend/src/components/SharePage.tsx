import { useEffect, useState } from "react";
import {
  AuthError,
  ShareGoneError,
  getSharePreview,
  getShareDownload,
} from "../api";

// Aufruf-Seite eines Freigabelinks (STOAA-193). Wird nur gerendert, wenn der
// Nutzer angemeldet ist (die Login-Pflicht erzwingt App.tsx davor). Zeigt
// ausschließlich die freigegebene Datei — Vorschau + Download des Originals,
// KEINE internen Metadaten oder Nachbardokumente (der Endpunkt liefert ohnehin
// nur die eine Datei). Ein widerrufener/abgelaufener Token (410) führt zur
// klaren Seite „Link nicht mehr gültig".

type PreviewState =
  | { kind: "loading" }
  | { kind: "ready"; url: string }
  | { kind: "gone" }
  | { kind: "error"; message: string };

export default function SharePage({
  token,
  onAuthLost,
}: {
  token: string;
  // Sitzung ist während des Abrufs verfallen (Refresh fehlgeschlagen) → App
  // schaltet auf die Anmeldung zurück; die URL /share/<token> bleibt erhalten.
  onAuthLost: () => void;
}) {
  const [state, setState] = useState<PreviewState>({ kind: "loading" });
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setState({ kind: "loading" });
    getSharePreview(token)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ kind: "ready", url: objectUrl });
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ShareGoneError) setState({ kind: "gone" });
        else if (err instanceof AuthError) onAuthLost();
        else
          setState({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
      });
    return () => {
      cancelled = true;
      // Blob-URL freigeben, damit der Speicher nicht leckt.
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [token, onAuthLost]);

  async function download() {
    setDownloading(true);
    setDownloadError(null);
    try {
      const { blob, filename } = await getShareDownload(token);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      // Der Link kann zwischen Vorschau und Download widerrufen/abgelaufen sein.
      if (err instanceof ShareGoneError) setState({ kind: "gone" });
      else if (err instanceof AuthError) onAuthLost();
      else setDownloadError(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  }

  if (state.kind === "gone") {
    return (
      <div className="login">
        <div className="card login-card share-message">
          <h1>Link nicht mehr gültig</h1>
          <p className="subtitle">
            Dieser Freigabelink wurde widerrufen oder ist abgelaufen. Bitte den
            Absender um einen neuen Link.
          </p>
        </div>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="login">
        <div className="card login-card share-message">
          <h1>Dokument nicht verfügbar</h1>
          <p className="subtitle">{state.message}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="shell share-page">
      <div className="topbar">
        <div>
          <h1>Geteiltes Dokument</h1>
          <p className="subtitle">Über einen Freigabelink bereitgestellt.</p>
        </div>
        <div className="topbar-right">
          <button onClick={download} disabled={downloading || state.kind !== "ready"}>
            {downloading ? "Herunterladen …" : "Original herunterladen"}
          </button>
        </div>
      </div>

      {downloadError && <p className="status status--error">{downloadError}</p>}

      {state.kind === "loading" ? (
        <p className="muted">Vorschau wird geladen …</p>
      ) : (
        <iframe
          className="pdf-frame"
          src={state.url}
          title="Vorschau des geteilten Dokuments"
        />
      )}
    </div>
  );
}
