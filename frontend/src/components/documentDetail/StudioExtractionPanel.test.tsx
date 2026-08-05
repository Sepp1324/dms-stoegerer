import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getExtractionCandidates = vi.fn();
const applyExtractionCandidate = vi.fn();
const dismissExtractionCandidate = vi.fn();
vi.mock("../../api", () => ({
  getExtractionCandidates: (...a: unknown[]) => getExtractionCandidates(...a),
  applyExtractionCandidate: (...a: unknown[]) => applyExtractionCandidate(...a),
  dismissExtractionCandidate: (...a: unknown[]) => dismissExtractionCandidate(...a),
}));

import { StudioExtractionPanel } from "./StudioExtractionPanel";

const cand = (over: Record<string, unknown>) => ({
  id: 1,
  document: 7,
  field: "amount",
  field_label: "Betrag",
  value: "42,00 EUR",
  normalized_value: "42.00",
  confidence: 82,
  reason: "Betrag erkannt",
  source: "heuristic",
  source_page: 1,
  source_version: 3,
  source_bbox: [10, 20, 30, 40],
  source_snippet: "",
  source_snippet_html: "",
  status: "pending",
  created_at: "",
  applied_at: null,
  dismissed_at: null,
  ...over,
});

beforeEach(() => {
  getExtractionCandidates.mockReset();
  applyExtractionCandidate.mockReset();
  dismissExtractionCandidate.mockReset();
});

describe("StudioExtractionPanel", () => {
  it("zeigt offene Vorschläge; Sprung nur mit Fundstelle", async () => {
    getExtractionCandidates.mockResolvedValue([
      cand({ id: 1, field_label: "Betrag", value: "42,00 EUR" }),
      cand({ id: 2, field_label: "IBAN", value: "AT..", source_page: null, source_bbox: null }),
    ]);
    const onJump = vi.fn();
    render(<StudioExtractionPanel documentId={7} onJump={onJump} />);

    await waitFor(() => expect(screen.getByText("42,00 EUR")).toBeInTheDocument());
    // Nur der verankerte Vorschlag hat einen „Im Beleg zeigen"-Button.
    const jumpButtons = screen.getAllByRole("button", { name: "Im Beleg zeigen" });
    expect(jumpButtons).toHaveLength(1);

    fireEvent.click(jumpButtons[0]);
    expect(onJump).toHaveBeenCalledWith({ page: 1, bbox: [10, 20, 30, 40] });
  });

  it("übernimmt einen Vorschlag und entfernt ihn", async () => {
    getExtractionCandidates.mockResolvedValue([cand({ id: 5 })]);
    applyExtractionCandidate.mockResolvedValue({});
    const onApplied = vi.fn();
    render(<StudioExtractionPanel documentId={7} onJump={vi.fn()} onApplied={onApplied} />);

    await waitFor(() => expect(screen.getByText("42,00 EUR")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Übernehmen" }));

    await waitFor(() => expect(applyExtractionCandidate).toHaveBeenCalledWith(7, 5));
    await waitFor(() => expect(screen.queryByText("42,00 EUR")).toBeNull());
    expect(onApplied).toHaveBeenCalled();
  });

  it("verwirft einen Vorschlag", async () => {
    getExtractionCandidates.mockResolvedValue([cand({ id: 9 })]);
    dismissExtractionCandidate.mockResolvedValue({});
    render(<StudioExtractionPanel documentId={7} onJump={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("42,00 EUR")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Verwerfen" }));

    await waitFor(() => expect(dismissExtractionCandidate).toHaveBeenCalledWith(7, 9));
    await waitFor(() => expect(screen.queryByText("42,00 EUR")).toBeNull());
  });

  it("zeigt einen leeren Zustand ohne offene Vorschläge", async () => {
    getExtractionCandidates.mockResolvedValue([]);
    render(<StudioExtractionPanel documentId={7} onJump={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByText(/Keine offenen Beleg-Daten/i)).toBeInTheDocument(),
    );
  });
});
