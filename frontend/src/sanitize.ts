// HTML-Sanitizing für Server-erzeugtes Markup, das per ``dangerouslySetInnerHTML``
// gerendert wird (STOAA-313). Team-Vorgabe: DOMPurify — niemals ungesäubertes
// Backend-HTML in den DOM schreiben.
//
// Aktuell einziger Anwendungsfall: die ``text_diff_html``-Tabelle des
// Versionsvergleichs (difflib ``HtmlDiff().make_table``). difflib escaped den
// eingebetteten OCR-Text zwar bereits, aber Sanitizing ist Defense-in-Depth:
// Sollte je ungeescapetes/anders erzeugtes HTML kommen, werden Skripte,
// Event-Handler und ``javascript:``-URLs entfernt, während die reine
// Diff-Tabelle (table/tr/td/span/a mit Fragment-Links + Klassen) erhalten bleibt.
import DOMPurify from "dompurify";

// Sanitized eine difflib-HtmlDiff-Tabelle. Ergebnis ist sicher für
// ``dangerouslySetInnerHTML``. Leerer/fehlender Input → leerer String.
export function sanitizeDiffHtml(html: string | null | undefined): string {
  if (!html) return "";
  return DOMPurify.sanitize(html, {
    // Nur die Elemente/Attribute, die eine difflib-Tabelle braucht.
    ALLOWED_TAGS: [
      "table",
      "thead",
      "tbody",
      "tr",
      "td",
      "th",
      "span",
      "a",
      "colgroup",
      "col",
    ],
    ALLOWED_ATTR: ["class", "id", "href", "colspan", "rowspan", "nowrap"],
    // Nur Fragment-/relative Links (difflib nutzt ``href="#..."`` zur Navigation).
    ALLOW_ARIA_ATTR: false,
  });
}

// Sanitized ein Suchergebnis-Snippet (STOAA-368/370). Das Backend liefert bereits
// sicheres HTML (nur ``<mark>``, Rest escaped) – DOMPurify ist Defense-in-Depth vor
// dem ``dangerouslySetInnerHTML``: erlaubt ausschließlich ``<mark>``, entfernt alles
// andere. Leerer/fehlender Input → leerer String.
export function sanitizeSnippet(html: string | null | undefined): string {
  if (!html) return "";
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ["mark"],
    ALLOWED_ATTR: [],
  });
}
