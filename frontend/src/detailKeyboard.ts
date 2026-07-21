// Reine Guard-Logik für "Escape schließt die Detailansicht". Escape geht NUR
// zurück, wenn man nicht gerade bearbeitet und nicht in einem Eingabefeld tippt
// (dort gehört Escape dem Feld/Formular).
export function shouldCloseOnEscape(
  key: string,
  editing: boolean,
  activeTag?: string | null,
  isContentEditable?: boolean,
): boolean {
  if (key !== "Escape") return false;
  if (editing) return false;
  const tag = (activeTag ?? "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return false;
  if (isContentEditable) return false;
  return true;
}
