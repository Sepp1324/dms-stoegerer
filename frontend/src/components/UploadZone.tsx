import { useRef, useState } from "react";
import { uploadDocument } from "../api";

type Status = "pending" | "uploading" | "done" | "error";
interface Item {
  name: string;
  status: Status;
  message?: string;
}

function statusLabel(item: Item): string {
  switch (item.status) {
    case "pending":
      return "…";
    case "uploading":
      return "lädt hoch …";
    case "done":
      return "✓ aufgenommen (OCR läuft)";
    case "error":
      return `Fehler: ${item.message ?? "unbekannt"}`;
  }
}

export default function UploadZone({ onUploaded }: { onUploaded: () => void }) {
  const [items, setItems] = useState<Item[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    const list = Array.from(files);
    setItems(list.map((f) => ({ name: f.name, status: "pending" as Status })));
    setBusy(true);

    for (let i = 0; i < list.length; i++) {
      setItems((prev) =>
        prev.map((it, idx) => (idx === i ? { ...it, status: "uploading" } : it)),
      );
      try {
        await uploadDocument(list[i]);
        setItems((prev) =>
          prev.map((it, idx) => (idx === i ? { ...it, status: "done" } : it)),
        );
      } catch (err) {
        setItems((prev) =>
          prev.map((it, idx) =>
            idx === i
              ? { ...it, status: "error", message: err instanceof Error ? err.message : String(err) }
              : it,
          ),
        );
      }
    }

    setBusy(false);
    onUploaded(); // Liste neu laden – die neuen Dokumente sind bereits angelegt.
  }

  return (
    <div
      className={`upload card ${dragOver ? "upload--over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        handleFiles(e.dataTransfer.files);
      }}
      onClick={() => !busy && inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        accept=".pdf,image/*"
        onChange={(e) => {
          handleFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <p className="upload-hint">
        Dateien hierher ziehen oder <span className="upload-link">klicken zum Auswählen</span>
        <br />
        <span className="muted">PDF und Bilder · OCR läuft anschließend automatisch</span>
      </p>

      {items.length > 0 && (
        <ul className="upload-list" onClick={(e) => e.stopPropagation()}>
          {items.map((it, i) => (
            <li key={i} className={`upload-item upload-item--${it.status}`}>
              <span className="upload-name">{it.name}</span>
              <span className="upload-status">{statusLabel(it)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
