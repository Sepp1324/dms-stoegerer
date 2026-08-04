import { useMemo } from "react";

import { scaleBox, type Bbox } from "./overlayGeometry";

// Deckungsgleiches OCR-Overlay über der gerenderten PDF.js-Seite (Phase 2).
// Zeichnet die Suchtreffer DIESER Seite als Rechtecke (schlankes DOM). Der aktive
// Treffer (Navigation) wird hervorgehoben und beim Wechsel in den Sichtbereich
// gescrollt. Rein visuell (pointer-events: none) – Markieren/Kopieren im PDF-
// Textlayer darunter bleibt möglich.
export function OcrOverlay({
  matches,
  activeIndex,
  layoutWidth,
  layoutHeight,
  renderedWidth,
  renderedHeight,
}: {
  matches: { bbox: Bbox }[];
  // Index innerhalb ``matches``, der gerade angesteuert ist (oder -1/undefined).
  activeIndex?: number;
  layoutWidth: number;
  layoutHeight: number;
  renderedWidth: number;
  renderedHeight: number;
}) {
  const boxes = useMemo(
    () =>
      matches
        .map((m, index) => ({
          index,
          rect: scaleBox(
            m.bbox,
            layoutWidth,
            layoutHeight,
            renderedWidth,
            renderedHeight,
          ),
        }))
        .filter((b): b is { index: number; rect: NonNullable<typeof b.rect> } =>
          b.rect !== null,
        ),
    [matches, layoutWidth, layoutHeight, renderedWidth, renderedHeight],
  );

  if (!renderedWidth || !renderedHeight) return null;

  return (
    <div
      className="ocr-overlay"
      style={{ width: renderedWidth, height: renderedHeight }}
      aria-hidden="true"
      data-match-count={boxes.length}
    >
      {boxes.map(({ index, rect }) => {
        const active = index === activeIndex;
        return (
          <span
            key={index}
            className={`ocr-word--match${active ? " ocr-word--active" : ""}`}
            style={{
              left: rect.left,
              top: rect.top,
              width: rect.width,
              height: rect.height,
            }}
            // Aktiven Treffer beim Erscheinen in den Sichtbereich rücken.
            ref={
              active
                ? (el) => el?.scrollIntoView({ block: "center", inline: "center" })
                : undefined
            }
          />
        );
      })}
    </div>
  );
}
