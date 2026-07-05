import { useEffect, useRef, useState } from "react";
import { uploadMobileCapture, type DocumentItem } from "../api";

// Mobile-Erfass-Ansicht (STOAA-514/512b, AK5): Kamera-Fotos aufnehmen,
// ordnen/löschen, optional betiteln und als EIN Dokument hochladen (Backend
// fügt die Bilder serverseitig zu einem PDF zusammen). Bewusst ohne dnd- oder
// UI-Bibliothek – Hoch/Runter-Buttons und einfache Vorschau-Thumbnails.

interface Shot {
  // Stabiler Key für React (Index als key wäre beim Umsortieren/Löschen fehleranfällig).
  key: number;
  file: File;
  url: string; // Object-URL für die Vorschau; wird beim Entfernen revoked.
}

type Phase = "idle" | "uploading" | "done" | "error";

let nextKey = 1;

export default function MobileCapture({
  canWrite,
  onUploaded,
}: {
  canWrite: boolean;
  onUploaded?: (doc: DocumentItem) => void;
}) {
  const [shots, setShots] = useState<Shot[]>([]);
  const [title, setTitle] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Ref spiegelt stets die aktuelle Liste, damit der Unmount-Cleanup auch die
  // erst später erzeugten Object-URLs freigibt (Closure über [] würde sonst nur
  // die leere Anfangsliste sehen).
  const shotsRef = useRef<Shot[]>(shots);
  shotsRef.current = shots;

  // Alle noch offenen Object-URLs beim Unmount freigeben (Speicherleck vermeiden).
  useEffect(() => {
    return () => {
      shotsRef.current.forEach((s) => URL.revokeObjectURL(s.url));
    };
  }, []);

  function addFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    const added = Array.from(files)
      .filter((f) => f.type.startsWith("image/") || f.type === "")
      .map((file) => ({ key: nextKey++, file, url: URL.createObjectURL(file) }));
    if (added.length === 0) return;
    // NICHT ersetzen: an die bestehende Liste anhängen (mehrere Fotos nacheinander).
    setShots((prev) => [...prev, ...added]);
    setPhase("idle");
    setMessage(null);
  }

  function removeAt(idx: number) {
    setShots((prev) => {
      const s = prev[idx];
      if (s) URL.revokeObjectURL(s.url);
      return prev.filter((_, i) => i !== idx);
    });
  }

  function move(idx: number, dir: -1 | 1) {
    setShots((prev) => {
      const to = idx + dir;
      if (to < 0 || to >= prev.length) return prev;
      const next = [...prev];
      [next[idx], next[to]] = [next[to], next[idx]];
      return next;
    });
  }

  async function upload() {
    if (shots.length === 0) return;
    setPhase("uploading");
    setMessage(null);
    try {
      const doc = await uploadMobileCapture(
        shots.map((s) => s.file),
        title,
      );
      // Vorschauen freigeben und Formular zurücksetzen.
      shots.forEach((s) => URL.revokeObjectURL(s.url));
      setShots([]);
      setTitle("");
      setPhase("done");
      setMessage(`„${doc.title}" wurde erfasst – OCR läuft.`);
      onUploaded?.(doc);
    } catch (err) {
      setPhase("error");
      setMessage(err instanceof Error ? err.message : String(err));
    }
  }

  if (!canWrite) {
    return (
      <div className="capture card">
        <p className="muted">
          Zum Erfassen fehlt die Schreibberechtigung. Bitte wende dich an eine
          Administratorin.
        </p>
      </div>
    );
  }

  const busy = phase === "uploading";

  return (
    <div className="capture">
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        // multiple erlaubt in einem Rutsch mehrere Bilder aus der Galerie;
        // die Kamera (capture) liefert i. d. R. genau eines pro Aufnahme.
        multiple
        onChange={(e) => {
          addFiles(e.target.files);
          e.target.value = ""; // gleiche Datei erneut wählbar machen
        }}
      />

      <div className="capture__actions">
        <button
          type="button"
          className="capture__add"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
        >
          <span aria-hidden="true">📷</span> Foto hinzufügen
        </button>
        <span className="muted capture__count">
          {shots.length === 0
            ? "Noch keine Seiten"
            : `${shots.length} Seite${shots.length === 1 ? "" : "n"}`}
        </span>
      </div>

      {shots.length > 0 && (
        <>
          <label className="capture__title-field">
            <span className="muted">Titel (optional)</span>
            <input
              type="text"
              placeholder="z. B. Kassenbon Baumarkt"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={busy}
            />
          </label>

          <ul className="capture__list">
            {shots.map((s, i) => (
              <li key={s.key} className="capture__item">
                <img className="capture__thumb" src={s.url} alt={`Seite ${i + 1}`} />
                <div className="capture__meta">
                  <span className="capture__page">Seite {i + 1}</span>
                  <span className="muted capture__name">{s.file.name || "Foto"}</span>
                </div>
                <div className="capture__reorder">
                  <button
                    type="button"
                    aria-label={`Seite ${i + 1} nach oben`}
                    onClick={() => move(i, -1)}
                    disabled={busy || i === 0}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    aria-label={`Seite ${i + 1} nach unten`}
                    onClick={() => move(i, 1)}
                    disabled={busy || i === shots.length - 1}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="capture__del"
                    aria-label={`Seite ${i + 1} löschen`}
                    onClick={() => removeAt(i)}
                    disabled={busy}
                  >
                    ✕
                  </button>
                </div>
              </li>
            ))}
          </ul>

          <button
            type="button"
            className="capture__upload"
            onClick={upload}
            disabled={busy}
          >
            {busy ? "Wird hochgeladen …" : "Hochladen"}
          </button>
        </>
      )}

      {message && (
        <p
          className={`capture__msg capture__msg--${phase === "error" ? "error" : "ok"}`}
          role={phase === "error" ? "alert" : "status"}
        >
          {message}
        </p>
      )}
    </div>
  );
}
