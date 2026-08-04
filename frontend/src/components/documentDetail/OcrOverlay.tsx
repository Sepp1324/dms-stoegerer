import { useMemo } from "react";

import { countMatches, positionWords, type LayoutWord } from "./overlayGeometry";

// Deckungsgleiches OCR-Overlay über der gerenderten PDF.js-Seite (Phase 2).
// Slice 1: hebt Suchtreffer hervor. Es werden NUR Treffer als Rechtecke gezeichnet
// (schlankes DOM); die restlichen Wörter bleiben über den auswählbaren Textlayer
// von PDF.js zugänglich. Das Overlay ist rein visuell (pointer-events: none), damit
// Markieren/Kopieren im PDF darunter weiter funktioniert.
export function OcrOverlay({
  words,
  layoutWidth,
  layoutHeight,
  renderedWidth,
  renderedHeight,
  query,
}: {
  words: LayoutWord[];
  layoutWidth: number;
  layoutHeight: number;
  renderedWidth: number;
  renderedHeight: number;
  query: string;
}) {
  const matches = useMemo(
    () =>
      positionWords(
        words,
        layoutWidth,
        layoutHeight,
        renderedWidth,
        renderedHeight,
        query,
      ).filter((w) => w.match),
    [words, layoutWidth, layoutHeight, renderedWidth, renderedHeight, query],
  );

  if (!renderedWidth || !renderedHeight) return null;

  return (
    <div
      className="ocr-overlay"
      style={{ width: renderedWidth, height: renderedHeight }}
      aria-hidden="true"
      data-match-count={matches.length}
    >
      {matches.map((w) => (
        <span
          key={w.index}
          className="ocr-word--match"
          style={{ left: w.left, top: w.top, width: w.width, height: w.height }}
        />
      ))}
    </div>
  );
}

export { countMatches, positionWords };
