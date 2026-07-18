// View <-> URL-Pfad (#7, Stage 1b). Die aktive Hauptansicht steht in der URL
// (/inbox, /dashboard, …); "dashboard" ist die Landing-Route "/". Bewusst als
// eigenes, pures Modul – so testbar und ohne die große DocumentsPage zu laden.
import type { CommandView } from "./components/CommandPalette";

export type MainView = CommandView;

export const ALL_VIEWS: MainView[] = [
  "dashboard",
  "docs",
  "cases",
  "dossiers",
  "contracts",
  "knowledge",
  "copilot",
  "inbox",
  "capture",
  "rules",
  "workflows",
  "fields",
  "mail",
  "evidence",
  "quality",
  "system",
  "faellig",
];

export const DEFAULT_VIEW: MainView = "dashboard";

export function viewToPath(v: MainView): string {
  return v === DEFAULT_VIEW ? "/" : `/${v}`;
}

export function pathToView(pathname: string): MainView {
  const seg = pathname.replace(/^\/+/, "").split("/")[0];
  if (!seg) return DEFAULT_VIEW;
  return (ALL_VIEWS as string[]).includes(seg) ? (seg as MainView) : DEFAULT_VIEW;
}
