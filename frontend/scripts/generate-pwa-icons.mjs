// Erzeugt die PWA-Icons (192/512 „any" + 512 „maskable") ohne externe
// Bild-Bibliothek: ein winziger PNG-Encoder auf Basis des in Node
// eingebauten zlib. Bewusst simpel gehalten – flaches Dark-Theme-Logo mit
// „DMS"-Dokumentglyphe. Aufruf: `node scripts/generate-pwa-icons.mjs`.
// Die Ausgabe landet in `public/icons/` und ist eingecheckt; das Skript
// muss also nur laufen, wenn sich das Logo ändern soll.
import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// Dark-Theme-Token (siehe src/index.css)
const BG = [15, 23, 42]; // --bg  #0f172a
const ACCENT = [59, 130, 246]; // --accent #3b82f6
const PAPER = [226, 232, 240]; // --text  #e2e8f0

function crc32(buf) {
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const typeBuf = Buffer.from(type, "ascii");
  const body = Buffer.concat([typeBuf, data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([len, body, crc]);
}

function encodePng(size, rgba) {
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // color type RGBA
  // 10..12 = compression/filter/interlace = 0
  // Scanlines mit Filter-Byte 0 pro Zeile
  const stride = size * 4;
  const raw = Buffer.alloc((stride + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (stride + 1)] = 0;
    rgba.copy(raw, y * (stride + 1) + 1, y * stride, y * stride + stride);
  }
  const idat = deflateSync(raw, { level: 9 });
  return Buffer.concat([
    sig,
    chunk("IHDR", ihdr),
    chunk("IDAT", idat),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// Zeichnet das Logo in einen RGBA-Puffer. `pad` = Rand in Prozent (Safe-Zone
// für maskable). `fullBleed` füllt den Hintergrund mit Akzentfarbe, damit auch
// nach dem Runden zum Kreis die Marke sichtbar bleibt.
function drawLogo(size, { pad, fullBleed }) {
  const buf = Buffer.alloc(size * size * 4);
  const bg = fullBleed ? ACCENT : BG;
  const set = (x, y, [r, g, b]) => {
    const i = (y * size + x) * 4;
    buf[i] = r;
    buf[i + 1] = g;
    buf[i + 2] = b;
    buf[i + 3] = 255;
  };
  for (let y = 0; y < size; y++)
    for (let x = 0; x < size; x++) set(x, y, bg);

  // Dokument-Rechteck (Blatt) zentriert innerhalb der Safe-Zone.
  const inset = Math.round(size * pad);
  const w = Math.round(size * 0.42);
  const h = Math.round(size * 0.54);
  const x0 = Math.round((size - w) / 2);
  const y0 = Math.round((size - h) / 2);
  const paper = fullBleed ? PAPER : PAPER;
  const rect = (rx, ry, rw, rh, col) => {
    for (let y = ry; y < ry + rh; y++)
      for (let x = rx; x < rx + rw; x++)
        if (x >= inset && x < size - inset && y >= inset && y < size - inset)
          set(x, y, col);
  };
  rect(x0, y0, w, h, paper);
  // Eselsohr / Akzentbalken oben.
  rect(x0, y0, w, Math.round(h * 0.16), ACCENT);
  // Textzeilen (Muted-Akzent) auf dem Blatt.
  const lineCol = ACCENT;
  const lh = Math.max(2, Math.round(h * 0.05));
  const gap = Math.round(h * 0.12);
  let ly = y0 + Math.round(h * 0.3);
  for (let n = 0; n < 3; n++) {
    rect(x0 + Math.round(w * 0.14), ly, Math.round(w * 0.72), lh, lineCol);
    ly += gap;
  }
  return buf;
}

const outDir = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "icons");
mkdirSync(outDir, { recursive: true });

const variants = [
  { name: "icon-192.png", size: 192, opts: { pad: 0.06, fullBleed: false } },
  { name: "icon-512.png", size: 512, opts: { pad: 0.06, fullBleed: false } },
  { name: "icon-maskable-512.png", size: 512, opts: { pad: 0.14, fullBleed: true } },
  { name: "apple-touch-icon.png", size: 180, opts: { pad: 0.02, fullBleed: true } },
];
for (const v of variants) {
  const png = encodePng(v.size, drawLogo(v.size, v.opts));
  writeFileSync(join(outDir, v.name), png);
  console.log("wrote", v.name, png.length, "bytes");
}
