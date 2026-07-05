import { useState, useCallback } from "react";
import { uploadImages } from "../api";
import type { DocumentItem } from "../api";

interface Props {
  onUploaded: (doc: DocumentItem) => void;
}

interface CapturedPhoto {
  id: number;
  file: File;
  previewUrl: string;
}

export default function CameraUpload({ onUploaded }: Props) {
  const [photos, setPhotos] = useState<CapturedPhoto[]>([]);
  const [title, setTitle] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [nextId, setNextId] = useState(0);

  const handleCapture = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      if (!files.length) return;
      const newPhotos = files.map((file, i) => ({
        id: nextId + i,
        file,
        previewUrl: URL.createObjectURL(file),
      }));
      setNextId((n) => n + files.length);
      setPhotos((prev) => [...prev, ...newPhotos]);
      setDone(false);
      setError(null);
      e.target.value = "";
    },
    [nextId],
  );

  const removePhoto = (id: number) => {
    setPhotos((prev) => {
      const p = prev.find((ph) => ph.id === id);
      if (p) URL.revokeObjectURL(p.previewUrl);
      return prev.filter((ph) => ph.id !== id);
    });
  };

  const moveUp = (idx: number) => {
    if (idx === 0) return;
    setPhotos((prev) => {
      const arr = [...prev];
      [arr[idx - 1], arr[idx]] = [arr[idx], arr[idx - 1]];
      return arr;
    });
  };

  const moveDown = (idx: number) => {
    setPhotos((prev) => {
      if (idx >= prev.length - 1) return prev;
      const arr = [...prev];
      [arr[idx], arr[idx + 1]] = [arr[idx + 1], arr[idx]];
      return arr;
    });
  };

  const handleUpload = async () => {
    if (!photos.length) return;
    setUploading(true);
    setError(null);
    try {
      const doc = await uploadImages(
        photos.map((p) => p.file),
        title.trim() || undefined,
      );
      photos.forEach((p) => URL.revokeObjectURL(p.previewUrl));
      setPhotos([]);
      setTitle("");
      setDone(true);
      onUploaded(doc);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="camera-upload">
      <h2 className="camera-upload__title">Foto-Upload</h2>
      <p className="camera-upload__hint">
        Fotografiere Belege mit der Kamera – mehrere Seiten werden zu einem PDF zusammengefügt.
      </p>

      <label
        className="camera-upload__capture-btn"
        aria-disabled={uploading ? "true" : "false"}
      >
        <input
          type="file"
          accept="image/*"
          capture="environment"
          multiple
          onChange={handleCapture}
          disabled={uploading}
          style={{ display: "none" }}
        />
        📷 Foto aufnehmen
      </label>

      {photos.length > 0 && (
        <>
          <p className="camera-upload__count">
            {photos.length} Seite{photos.length !== 1 ? "n" : ""} ausgewählt
          </p>

          <div className="camera-upload__thumbnails">
            {photos.map((photo, idx) => (
              <div key={photo.id} className="camera-upload__thumb">
                <img
                  src={photo.previewUrl}
                  alt={`Seite ${idx + 1}`}
                  className="camera-upload__thumb-img"
                />
                <span className="camera-upload__thumb-page">{idx + 1}</span>
                <div className="camera-upload__thumb-actions">
                  <button
                    onClick={() => moveUp(idx)}
                    disabled={idx === 0 || uploading}
                    title="Nach vorne"
                    aria-label="Seite nach vorne"
                  >
                    ↑
                  </button>
                  <button
                    onClick={() => moveDown(idx)}
                    disabled={idx === photos.length - 1 || uploading}
                    title="Nach hinten"
                    aria-label="Seite nach hinten"
                  >
                    ↓
                  </button>
                  <button
                    onClick={() => removePhoto(photo.id)}
                    disabled={uploading}
                    title="Entfernen"
                    aria-label="Seite entfernen"
                    className="camera-upload__remove"
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>

          <input
            type="text"
            className="camera-upload__title-input"
            placeholder="Titel (optional)"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={uploading}
            maxLength={200}
          />

          <button
            className="camera-upload__upload-btn"
            onClick={handleUpload}
            disabled={uploading}
          >
            {uploading
              ? "Wird hochgeladen …"
              : `${photos.length} Seite${photos.length !== 1 ? "n" : ""} hochladen`}
          </button>
        </>
      )}

      {done && (
        <p className="camera-upload__success">✓ Dokument aufgenommen (OCR läuft)</p>
      )}
      {error && <p className="camera-upload__error">Fehler: {error}</p>}
    </div>
  );
}
