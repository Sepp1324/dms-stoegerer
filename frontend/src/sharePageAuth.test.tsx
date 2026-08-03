import { afterEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";

import SharePage from "./components/SharePage";
import * as api from "./api";

// P2: Ein Sitzungswechsel (AuthSessionChangedError) darf SharePage NICHT ausloggen
// – die neue Sitzung ist gültig. Nur ein echter AuthError (abgelaufen) schaltet
// die UI auf abgemeldet.
vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, getSharePreview: vi.fn(), getShareDownload: vi.fn() };
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.mocked(api.getSharePreview).mockReset();
});

describe("SharePage Auth-Verhalten", () => {
  it("loggt bei einem Sitzungswechsel während der Vorschau NICHT aus", async () => {
    vi.mocked(api.getSharePreview).mockRejectedValue(
      new api.AuthSessionChangedError("gewechselt"),
    );
    const onAuthLost = vi.fn();
    render(<SharePage token="t" onAuthLost={onAuthLost} />);

    await waitFor(() => expect(api.getSharePreview).toHaveBeenCalled());
    // Dem catch Zeit geben.
    await new Promise((r) => setTimeout(r, 0));
    expect(onAuthLost).not.toHaveBeenCalled();
  });

  it("loggt bei einem echten AuthError (Sitzung abgelaufen) aus", async () => {
    vi.mocked(api.getSharePreview).mockRejectedValue(new api.AuthError("abgelaufen"));
    const onAuthLost = vi.fn();
    render(<SharePage token="t" onAuthLost={onAuthLost} />);

    await waitFor(() => expect(onAuthLost).toHaveBeenCalled());
  });
});
