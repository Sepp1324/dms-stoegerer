import {
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  getDocuments,
  type DocumentItem,
  type SavedView,
} from "../api";
import { ProcessingBadge } from "./ProcessingStatus";

export type CommandView =
  | "dashboard"
  | "docs"
  | "cases"
  | "dossiers"
  | "contracts"
  | "knowledge"
  | "copilot"
  | "inbox"
  | "capture"
  | "rules"
  | "workflows"
  | "fields"
  | "mail"
  | "evidence"
  | "quality"
  | "system"
  | "faellig";

export type CommandPreset = "latest" | "processing" | "failed" | "unfiled" | "inbox" | "quality";

type CommandTone = "neutral" | "warn" | "danger" | "ok";

type CommandItem = {
  id: string;
  group: string;
  title: string;
  subtitle: string;
  keywords: string[];
  tone?: CommandTone;
  renderMeta?: () => ReactNode;
  run: () => void;
};

function matches(item: CommandItem, query: string): boolean {
  if (!query) return true;
  const haystack = [item.title, item.subtitle, ...item.keywords].join(" ").toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function dateLabel(value: string | null): string {
  if (!value) return "ohne Datum";
  return new Date(value).toLocaleDateString("de-DE");
}

export default function CommandPalette({
  open,
  onOpenChange,
  canWrite,
  isAdmin,
  onNavigate,
  onApplyPreset,
  onApplySavedView,
  onOpenDocument,
  savedViews = [],
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canWrite: boolean;
  isAdmin: boolean;
  onNavigate: (view: CommandView) => void;
  onApplyPreset: (preset: CommandPreset) => void;
  onApplySavedView?: (view: SavedView) => void;
  onOpenDocument: (documentId: number) => void;
  savedViews?: SavedView[];
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState("");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [docLoading, setDocLoading] = useState(false);
  const [docError, setDocError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const key = event.key.toLowerCase();
      if ((event.metaKey || event.ctrlKey) && key === "k") {
        event.preventDefault();
        onOpenChange(true);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onOpenChange]);

  useEffect(() => {
    if (!open) return;
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open]);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setActiveIndex(0);
      return;
    }
    let active = true;
    setDocLoading(true);
    setDocError(null);
    const id = window.setTimeout(() => {
      getDocuments({
        q: query.trim(),
        ordering: query.trim() ? "" : "-added_at",
        page: 1,
      })
        .then((result) => {
          if (!active) return;
          setDocuments(result.results.slice(0, 8));
        })
        .catch((error) => {
          if (!active) return;
          setDocError(error instanceof Error ? error.message : String(error));
          setDocuments([]);
        })
        .finally(() => {
          if (active) setDocLoading(false);
        });
    }, query.trim() ? 180 : 0);
    return () => {
      active = false;
      window.clearTimeout(id);
    };
  }, [open, query]);

  const staticItems = useMemo<CommandItem[]>(() => {
    const navigate = (
      id: string,
      title: string,
      subtitle: string,
      view: CommandView,
      keywords: string[] = [],
      tone: CommandTone = "neutral",
    ): CommandItem => ({
      id,
      group: "Bereiche",
      title,
      subtitle,
      keywords,
      tone,
      run: () => {
        onNavigate(view);
        onOpenChange(false);
      },
    });

    const preset = (
      id: string,
      title: string,
      subtitle: string,
      value: CommandPreset,
      keywords: string[] = [],
      tone: CommandTone = "neutral",
    ): CommandItem => ({
      id,
      group: "Schnellansichten",
      title,
      subtitle,
      keywords,
      tone,
      run: () => {
        onApplyPreset(value);
        onOpenChange(false);
      },
    });

    const savedViewItems = savedViews.map(
      (view): CommandItem => ({
        id: `saved-view-${view.id}`,
        group: "Smart Views",
        title: view.name,
        subtitle: `${view.count} Dokument${view.count === 1 ? "" : "e"} · gespeicherte Filteransicht`,
        keywords: [
          "smart view",
          "gespeicherte ansicht",
          view.description,
          JSON.stringify(view.query),
        ],
        tone: view.is_default ? "ok" : "neutral",
        run: () => {
          onApplySavedView?.(view);
          onOpenChange(false);
        },
      }),
    );

    return [
      navigate("view-dashboard", "Cockpit öffnen", "Startansicht mit allen Signalen", "dashboard", ["dashboard", "home"]),
      navigate("view-docs", "Dokumente öffnen", "Archiv und Volltextsuche", "docs", ["archiv", "liste"]),
      navigate("view-copilot", "Copilot öffnen", "Fragen an das Archiv stellen", "copilot", ["ki", "chat", "frage"]),
      navigate("view-inbox", "Inbox öffnen", "Review, Vorschläge und Triage", "inbox", ["review", "prüfen"], "warn"),
      navigate("view-quality", "Qualität öffnen", "OCR, Metadaten und Archivmängel", "quality", ["mängel", "ocr"], "warn"),
      navigate("view-deadlines", "Fristen öffnen", "Wiedervorlagen und Termine", "faellig", ["wiedervorlage", "termin"], "warn"),
      navigate("view-contracts", "Verträge öffnen", "Verträge und Kündigungsfristen", "contracts", ["kündigung", "vertrag"]),
      navigate("view-cases", "Akten öffnen", "Fallakten und Dokumentgruppen", "cases", ["case", "akte"]),
      navigate("view-dossiers", "Dossiers öffnen", "Generierte Themen-Dossiers", "dossiers", ["dossier"]),
      navigate("view-evidence", "Beweis-Center öffnen", "Revision, Hashes und Nachweise", "evidence", ["audit", "hash", "revision"]),
      navigate("view-knowledge", "Gedächtnis öffnen", "Entitäten und Beziehungen", "knowledge", ["entity", "graph"]),
      ...(canWrite
        ? [
            navigate("view-capture", "Dokument erfassen", "Scan, Foto oder Datei hochladen", "capture", ["upload", "scan"], "ok"),
            navigate("view-rules", "Regeln öffnen", "Klassifizierung und Automatisierung", "rules", ["klassifizierung"]),
            navigate("view-workflows", "Workflows öffnen", "Automatische Aktionen verwalten", "workflows", ["automation"]),
          ]
        : []),
      ...(isAdmin
        ? [
            navigate("view-system", "Systemstatus öffnen", "Backup, OCR und Archiv prüfen", "system", ["backup", "restore"], "warn"),
            navigate("view-mail", "E-Mail-Center öffnen", "Importierte Nachrichten und IMAP", "mail", ["imap", "mail"]),
            navigate("view-fields", "Zusatzfelder öffnen", "Eigene Metadaten verwalten", "fields", ["custom fields"]),
          ]
        : []),
      preset("preset-latest", "Neueste Dokumente", "Dokumentliste nach Importdatum", "latest", ["aktuell", "neu"]),
      preset("preset-processing", "In Verarbeitung", "Pipeline läuft oder wartet", "processing", ["ocr", "pipeline"], "warn"),
      preset("preset-failed", "Fehlgeschlagene Verarbeitung", "Dokumente mit Retry-Bedarf", "failed", ["error", "fehler"], "danger"),
      preset("preset-unfiled", "Ohne Ordner", "Noch nicht einsortierte Dokumente", "unfiled", ["ordnerlos"], "warn"),
      ...savedViewItems,
    ];
  }, [canWrite, isAdmin, onApplyPreset, onApplySavedView, onNavigate, onOpenChange, savedViews]);

  const documentItems = useMemo<CommandItem[]>(
    () =>
      documents.map((doc) => ({
        id: `doc-${doc.id}`,
        group: query.trim() ? "Dokumente" : "Neue Dokumente",
        title: doc.title,
        subtitle: [
          `#${doc.id}`,
          doc.correspondent_name,
          doc.document_type_name,
          dateLabel(doc.created_at || doc.added_at),
        ]
          .filter(Boolean)
          .join(" · "),
        keywords: [
          String(doc.id),
          doc.correspondent_name ?? "",
          doc.document_type_name ?? "",
          doc.folder_path ?? "",
          ...doc.tags.map((tag) => tag.name),
        ],
        renderMeta: () => <ProcessingBadge state={doc.processing_state} />,
        run: () => {
          onOpenDocument(doc.id);
          onOpenChange(false);
        },
      })),
    [documents, onOpenChange, onOpenDocument, query],
  );

  const items = useMemo(() => {
    const q = query.trim();
    const staticMatches = staticItems.filter((item) => matches(item, q));
    return [...staticMatches, ...documentItems];
  }, [documentItems, query, staticItems]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query, items.length]);

  if (!open) return null;

  const activeItem = items[activeIndex] ?? null;

  function runActive() {
    activeItem?.run();
  }

  function onPaletteKeyDown(event: ReactKeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      onOpenChange(false);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(items.length - 1, index + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(0, index - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      runActive();
    }
  }

  const grouped = items.reduce<Record<string, CommandItem[]>>((acc, item) => {
    acc[item.group] = [...(acc[item.group] ?? []), item];
    return acc;
  }, {});
  let index = -1;

  return (
    <div className="command-layer" role="presentation" onKeyDown={onPaletteKeyDown}>
      <button
        type="button"
        className="command-backdrop"
        aria-label="Command Palette schließen"
        onClick={() => onOpenChange(false)}
      />
      <section
        className="command-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Command Palette"
      >
        <div className="command-search">
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path
              fill="currentColor"
              d="M10 4a6 6 0 0 1 4.8 9.6l4.3 4.3-1.4 1.4-4.3-4.3A6 6 0 1 1 10 4m0 2a4 4 0 1 0 0 8 4 4 0 0 0 0-8"
            />
          </svg>
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Aktion oder Dokument suchen …"
            aria-label="Aktion oder Dokument suchen"
          />
        </div>

        <div className="command-results" role="listbox" aria-label="Suchergebnisse">
          {Object.entries(grouped).map(([group, groupItems]) => (
            <div className="command-group" key={group}>
              <h3>{group}</h3>
              {groupItems.map((item) => {
                index += 1;
                const active = index === activeIndex;
                const itemIndex = index;
                return (
                  <button
                    type="button"
                    key={item.id}
                    className={`command-item command-item--${item.tone ?? "neutral"}${active ? " command-item--active" : ""}`}
                    role="option"
                    aria-selected={active}
                    onMouseEnter={() => setActiveIndex(itemIndex)}
                    onClick={() => item.run()}
                  >
                    <span className="command-item__mark" aria-hidden="true" />
                    <span className="command-item__body">
                      <strong>{item.title}</strong>
                      <span>{item.subtitle}</span>
                    </span>
                    {item.renderMeta && (
                      <span className="command-item__meta">{item.renderMeta()}</span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
          {items.length === 0 && (
            <div className="command-empty">
              {docLoading ? "Suche läuft …" : "Keine Aktion oder kein Dokument gefunden."}
            </div>
          )}
          {docError && <div className="command-error">{docError}</div>}
        </div>
      </section>
    </div>
  );
}
