// Reine Geometrie fürs OCR-Overlay (Phase 2). Bewusst ohne React, damit die
// Koordinaten-Abbildung isoliert testbar ist.
//
// Wortkästen kommen in PDF-Punkten (Ursprung oben-links) im Bezugssystem der
// Seitenmaße (layoutW/layoutH). PDF.js rendert die Seite in renderedW/renderedH
// CSS-Pixeln. Beide teilen dasselbe MediaBox-System (oben-links), daher genügt eine
// lineare Skalierung pro Achse. (Das Matching läuft serverseitig, siehe API.)

export type Bbox = [number, number, number, number];

export interface ScaledBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function scaleBox(
  bbox: Bbox,
  layoutW: number,
  layoutH: number,
  renderedW: number,
  renderedH: number,
): ScaledBox | null {
  if (!layoutW || !layoutH || !renderedW || !renderedH) return null;
  if (!bbox || bbox.length < 4) return null;
  const rx = renderedW / layoutW;
  const ry = renderedH / layoutH;
  const [x0, y0, x1, y1] = bbox;
  return {
    left: x0 * rx,
    top: y0 * ry,
    width: (x1 - x0) * rx,
    height: (y1 - y0) * ry,
  };
}
