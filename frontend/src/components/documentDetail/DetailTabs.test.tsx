import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TabPanel } from "./DetailTabs";

// Regression zu #2 (Lazy + Keep-alive): Panels feuern beim Öffnen keine Requests,
// solange sie nicht aktiv waren; nach dem ersten Besuch bleiben sie gemountet.
describe("TabPanel (lazy + keep-alive)", () => {
  it("rendert Kinder erst, wenn das Panel zum ersten Mal aktiv ist", () => {
    const { rerender } = render(
      <TabPanel id="briefing" active="overview">
        <span>PANEL_INHALT</span>
      </TabPanel>,
    );
    // Inaktiv, nie besucht -> Kinder NICHT im DOM (kein Mount, keine Requests).
    expect(screen.queryByText("PANEL_INHALT")).toBeNull();

    rerender(
      <TabPanel id="briefing" active="briefing">
        <span>PANEL_INHALT</span>
      </TabPanel>,
    );
    expect(screen.getByText("PANEL_INHALT")).toBeInTheDocument();
  });

  it("hält Kinder nach dem Wegschalten gemountet (keep-alive)", () => {
    const { rerender } = render(
      <TabPanel id="briefing" active="briefing">
        <span>PANEL_INHALT</span>
      </TabPanel>,
    );
    expect(screen.getByText("PANEL_INHALT")).toBeInTheDocument();

    rerender(
      <TabPanel id="briefing" active="overview">
        <span>PANEL_INHALT</span>
      </TabPanel>,
    );
    // Weiterhin im DOM (nur per hidden ausgeblendet) -> Zustand bleibt erhalten.
    expect(screen.getByText("PANEL_INHALT")).toBeInTheDocument();
  });
});
