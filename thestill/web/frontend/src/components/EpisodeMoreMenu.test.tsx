import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import EpisodeMoreMenu from "./EpisodeMoreMenu";
import { ToastProvider } from "./Toast";

function setup(onReprocess?: () => void) {
  render(
    <ToastProvider>
      <EpisodeMoreMenu
        shareTitle="Ep"
        shareUrl="https://thestill.me/ep"
        canPlayFromStart={false}
        onPlayFromStart={vi.fn()}
        played={false}
        onMarkPlayed={vi.fn().mockResolvedValue(undefined)}
        onReprocess={onReprocess}
      />
    </ToastProvider>,
  );
}

describe("EpisodeMoreMenu — Reprocess (spec #106)", () => {
  it("has no Reprocess item unless the reader passes a handler", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(
      screen.queryByRole("button", { name: "Reprocess…" }),
    ).not.toBeInTheDocument();
  });

  it("closes the menu and calls the handler", async () => {
    const onReprocess = vi.fn();
    setup(onReprocess);
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    await userEvent.click(screen.getByRole("button", { name: "Reprocess…" }));
    expect(onReprocess).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("group", { name: "More actions" }),
    ).not.toBeInTheDocument();
  });
});
