import { useEffect, useState } from "react";

import {
  applyExtractionCandidate,
  dismissExtractionCandidate,
  getExtractionCandidates,
  type ExtractionCandidate,
} from "../../api";

export interface HighlightTarget {
  page: number;
  bbox: [number, number, number, number];
}

// Beleg-Daten-Panel des Dokument-Studios (Phase 2, Slice 3b): zeigt die
// verankerten Extraktionsvorschläge (Betrag/Datum/IBAN/…). Jeder Vorschlag mit
// Fundstelle (source_page + source_bbox) lässt sich im Beleg anspringen; Übernehmen
// schreibt den Wert als Metadatum, Verwerfen blendet ihn aus. Die Übernahme bleibt
// eine EXPLIZITE Nutzeraktion – nie eine stille Änderung.
export function StudioExtractionPanel({
  documentId,
  onJump,
  onApplied,
  customFields,
  onAdoptToField,
}: {
  documentId: number;
  onJump: (target: HighlightTarget) => void;
  onApplied?: () => void;
  // Zusatzfelder des Dokuments + Handler, um einen Vorschlag als Zusatzfeld-Wert zu
  // übernehmen (statt in das feste Standardfeld). Optional – ohne bleibt nur „Übernehmen".
  customFields?: { id: number; name: string }[];
  onAdoptToField?: (c: ExtractionCandidate, fieldId: number) => Promise<void>;
}) {
  const [candidates, setCandidates] = useState<ExtractionCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setCandidates(null);
    setError(null);
    getExtractionCandidates(documentId)
      .then((list) => {
        if (alive) setCandidates(list.filter((c) => c.status === "pending"));
      })
      .catch(() => {
        if (alive) setError("Vorschläge konnten nicht geladen werden.");
      });
    return () => {
      alive = false;
    };
  }, [documentId]);

  const remove = (id: number) =>
    setCandidates((cs) => (cs ? cs.filter((c) => c.id !== id) : cs));

  const apply = async (c: ExtractionCandidate) => {
    setBusyId(c.id);
    setError(null);
    try {
      await applyExtractionCandidate(documentId, c.id);
      remove(c.id);
      onApplied?.();
    } catch {
      setError("Übernahme fehlgeschlagen.");
    } finally {
      setBusyId(null);
    }
  };

  const dismiss = async (c: ExtractionCandidate) => {
    setBusyId(c.id);
    setError(null);
    try {
      await dismissExtractionCandidate(documentId, c.id);
      remove(c.id);
    } catch {
      setError("Verwerfen fehlgeschlagen.");
    } finally {
      setBusyId(null);
    }
  };

  const adoptToField = async (c: ExtractionCandidate, fieldId: number) => {
    if (!onAdoptToField) return;
    setBusyId(c.id);
    setError(null);
    try {
      await onAdoptToField(c, fieldId);
      remove(c.id);
    } catch {
      setError("Übernahme ins Zusatzfeld fehlgeschlagen.");
    } finally {
      setBusyId(null);
    }
  };

  const canLocate = (c: ExtractionCandidate) =>
    c.source_page != null &&
    Array.isArray(c.source_bbox) &&
    c.source_bbox.length === 4;

  if (error && !candidates) {
    return <p className="status status--warn">{error}</p>;
  }
  if (!candidates) {
    return <p className="muted">Lade Beleg-Daten …</p>;
  }
  if (candidates.length === 0) {
    return (
      <p className="muted">
        Keine offenen Beleg-Daten. Erkannte Beträge, Daten, IBAN oder Nummern
        erscheinen hier zum Übernehmen.
      </p>
    );
  }

  return (
    <div className="studio-extract">
      {error && <p className="status status--warn">{error}</p>}
      <ul className="studio-extract__list">
        {candidates.map((c) => (
          <li key={c.id} className="studio-extract__item">
            <div className="studio-extract__head">
              <span className="studio-extract__field">{c.field_label}</span>
              <span
                className="studio-extract__conf"
                title="Konfidenz der Erkennung"
              >
                {c.confidence}%
              </span>
            </div>
            <div className="studio-extract__value">{c.value}</div>
            {c.reason && <div className="studio-extract__reason">{c.reason}</div>}
            <div className="studio-extract__actions">
              {canLocate(c) && (
                <button
                  type="button"
                  className="link"
                  onClick={() =>
                    onJump({
                      page: c.source_page as number,
                      bbox: c.source_bbox as [number, number, number, number],
                    })
                  }
                >
                  Im Beleg zeigen
                </button>
              )}
              <button
                type="button"
                disabled={busyId === c.id}
                onClick={() => apply(c)}
              >
                Übernehmen
              </button>
              <button
                type="button"
                className="link link--danger"
                disabled={busyId === c.id}
                onClick={() => dismiss(c)}
              >
                Verwerfen
              </button>
              {onAdoptToField && customFields && customFields.length > 0 && (
                <select
                  className="studio-extract__tofield"
                  aria-label={`„${c.value}" in Zusatzfeld übernehmen`}
                  value=""
                  disabled={busyId === c.id}
                  onChange={(e) => {
                    const fieldId = Number(e.target.value);
                    if (fieldId) adoptToField(c, fieldId);
                  }}
                >
                  <option value="">In Zusatzfeld …</option>
                  {customFields.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
