import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import ReprocessStatus from "./ReprocessStatus";
import type { EpisodeTask } from "../api/types";

let mockIsAdmin = true;
vi.mock("../contexts/AuthContext", () => ({
  useAuth: () => ({ isAdmin: mockIsAdmin }),
}));

const REPROCESS = {
  run_full_pipeline: true,
  initiated_by: "reprocess",
  reprocess: {
    from_stage: "clean" as const,
    refresh_episode_facts: false,
    requested_by: "u1",
    requested_at: "2026-10-21T12:00:00Z",
  },
};

function task(
  stage: string,
  status: string,
  metadata: EpisodeTask["metadata"] = REPROCESS,
): EpisodeTask {
  return { id: `${stage}-${status}`, stage, status, metadata } as EpisodeTask;
}

describe("ReprocessStatus (spec #106)", () => {
  beforeEach(() => {
    mockIsAdmin = true;
  });

  it("reports the running user-chain stage of a reprocess", () => {
    render(<ReprocessStatus tasks={[task("clean", "processing")]} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Reprocessing from clean: cleaning the transcript.",
    );
  });

  it("says when the stage is still queued", () => {
    render(<ReprocessStatus tasks={[task("summarize", "pending")]} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "summarizing (queued)",
    );
  });

  it("leaves the entity branch to its own strip", () => {
    const { container } = render(
      <ReprocessStatus tasks={[task("reindex", "processing")]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("ignores first-pass work and finished stages", () => {
    const { container } = render(
      <ReprocessStatus
        tasks={[
          task("clean", "processing", { run_full_pipeline: true }),
          task("summarize", "completed"),
        ]}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("is admin-only", () => {
    mockIsAdmin = false;
    const { container } = render(
      <ReprocessStatus tasks={[task("clean", "processing")]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
