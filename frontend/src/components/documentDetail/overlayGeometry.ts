// Reine Geometrie-/Matching-Logik fürs OCR-Overlay (Phase 2). Bewusst ohne React,
// damit die Koordinaten-Abbildung isoliert testbar ist.
//
// Die Wortkästen kommen in PDF-Punkten (Ursprung oben-links) im Bezugssystem der
// Seitenmaße (layoutW/layoutH). Die Seite wird von PDF.js in renderedW/renderedH
// CSS-Pixeln gerendert. Da beide dasselbe MediaBox-Koordinatensystem (oben-links)
// verwenden, genügt eine lineare Skalierung pro Achse.

export interface LayoutWord {
  t: string;
  bbox: [number, number, number, number];
}

export interface PositionedWord {
  t: string;
  index: number;
  left: number;
  top: number;
  width: number;
  height: number;
  match: boolean;
}

// Normalisierung für die Suche: Groß/Klein egal, Diakritika entfernt (Ö→O),
// damit "muller" auch "Müller" trifft. Kein Regex vom Nutzer – reines includes().
export function normalizeText(s: string): string {
  return (s || "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
}

export function positionWords(
  words: LayoutWord[],
  layoutW: number,
  layoutH: number,
  renderedW: number,
  renderedH: number,
  query: string,
): PositionedWord[] {
  if (!layoutW || !layoutH || !renderedW || !renderedH) return [];
  const rx = renderedW / layoutW;
  const ry = renderedH / layoutH;
  const q = normalizeText(query.trim());
  return words.map((w, index) => {
    const [x0, y0, x1, y1] = w.bbox;
    return {
      t: w.t,
      index,
      left: x0 * rx,
      top: y0 * ry,
      width: (x1 - x0) * rx,
      height: (y1 - y0) * ry,
      match: q.length > 0 && normalizeText(w.t).includes(q),
    };
  });
}

export function countMatches(words: PositionedWord[]): number {
  let n = 0;
  for (const w of words) if (w.match) n++;
  return n;
}
