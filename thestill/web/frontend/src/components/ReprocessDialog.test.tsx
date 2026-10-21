import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReprocessDialog from "./ReprocessDialog";

function setup(
  props: Partial<React.ComponentProps<typeof ReprocessDialog>> = {},
) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ReprocessDialog
      isOpen
      episodeTitle="581. Trump and Putin vs Zelensky"
      duration="1:12:04"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...props}
    />,
  );
  return { onConfirm, onCancel };
}

describe("ReprocessDialog (spec #106)", () => {
  it("defaults to clean and sends it without a facts refresh", async () => {
    const { onConfirm } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Re-clean" }));
    expect(onConfirm).toHaveBeenCalledWith({
      from_stage: "clean",
      refresh_episode_facts: false,
    });
  });

  it("sends the facts refresh when ticked", async () => {
    const { onConfirm } = setup();
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: "Refresh speaker names and guests",
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Re-clean" }));
    expect(onConfirm).toHaveBeenCalledWith({
      from_stage: "clean",
      refresh_episode_facts: true,
    });
  });

  it("names the cost of re-transcribing in the warning and the button", async () => {
    const { onConfirm } = setup();
    await userEvent.click(screen.getByRole("radio", { name: /Transcribe/ }));
    expect(
      screen.getByText("This pays for transcribing 1:12:04 of audio again."),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Re-transcribe 1:12:04" }),
    );
    expect(onConfirm).toHaveBeenCalledWith({
      from_stage: "transcribe",
      refresh_episode_facts: false,
    });
  });

  it("offers the facts refresh only where facts are extracted", async () => {
    setup();
    await userEvent.click(screen.getByRole("radio", { name: /Summarize/ }));
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /Entities/ }));
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("never sends a facts refresh for a stage that does not use it", async () => {
    const { onConfirm } = setup();
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: "Refresh speaker names and guests",
      }),
    );
    await userEvent.click(screen.getByRole("radio", { name: /Summarize/ }));
    await userEvent.click(screen.getByRole("button", { name: "Re-summarize" }));
    expect(onConfirm).toHaveBeenCalledWith({
      from_stage: "summarize",
      refresh_episode_facts: false,
    });
  });

  it("shows a refusal from the server", () => {
    setup({ error: "This episode already has a task queued or running" });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "already has a task queued or running",
    );
  });
});
