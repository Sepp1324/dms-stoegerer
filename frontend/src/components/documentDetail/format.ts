// Gemeinsame Anzeige-Helfer der DocumentDetail-Panels (STOAA-431, aus
// DocumentDetail.tsx extrahiert – Verhalten unverändert).

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Reines Datum ("YYYY-MM-DD") ohne Zeitzonen-Verschiebung als "DD.MM.YYYY"
// formatieren (new Date("YYYY-MM-DD") wäre UTC-Mitternacht und könnte lokal um
// einen Tag springen).
export function formatDateOnly(date: string): string {
  const m = date.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : date;
}

// ISO-Belegdatum (YYYY-MM-DD) menschenlesbar; ungültige Werte unverändert lassen.
export function formatIsoDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("de-DE", {
    day: "2-digit",
    month: "long",
    year: "numeric",
  });
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

// SHA-256 für die Anzeige kürzen (Anfang…Ende); leere Hashes als "—".
export function shortHash(hash: string): string {
  if (!hash) return "—";
  return hash.length > 20 ? `${hash.slice(0, 10)}…${hash.slice(-6)}` : hash;
}
