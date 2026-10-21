import { useEffect, useState } from "react";
import ConfirmDialog from "./ConfirmDialog";
import type { EpisodeReprocessRequest, ReprocessFromStage } from "../api/types";

// Spec #106 §4 — admin-only "Reprocess…" for an episode that has already
// been through the pipeline. The chosen stage and every stage after it run
// again; readers keep the current transcript and summary meanwhile.

interface StageOption {
  stage: ReprocessFromStage;
  label: string;
  detail: string;
  /** Shows the "refresh speaker names and guests" option. */
  facts: boolean;
}

const STAGES: StageOption[] = [
  {
    stage: "transcribe",
    label: "Transcribe",
    detail:
      "Calls the transcription provider again, then cleans, summarizes and re-indexes.",
    facts: true,
  },
  {
    stage: "clean",
    label: "Clean",
    detail:
      "Re-cleans the transcript with the current prompt, then summarizes and re-indexes.",
    facts: true,
  },
  {
    stage: "summarize",
    label: "Summarize",
    detail: "Writes a new summary, then re-indexes people, topics and search.",
    facts: false,
  },
  {
    stage: "entities",
    label: "Entities",
    detail:
      "Re-extracts people, companies and topics, and rebuilds search and related episodes.",
    facts: false,
  },
];

function confirmLabel(
  stage: ReprocessFromStage,
  duration: string | null,
): string {
  switch (stage) {
    case "transcribe":
      return duration ? `Re-transcribe ${duration}` : "Re-transcribe";
    case "clean":
      return "Re-clean";
    case "summarize":
      return "Re-summarize";
    case "entities":
      return "Re-extract entities";
  }
}

export interface ReprocessDialogProps {
  isOpen: boolean;
  episodeTitle: string;
  /** Human-readable duration, shown with the transcription cost warning. */
  duration: string | null;
  busy?: boolean;
  error?: string | null;
  onConfirm: (request: EpisodeReprocessRequest) => void;
  onCancel: () => void;
}

export default function ReprocessDialog({
  isOpen,
  episodeTitle,
  duration,
  busy,
  error,
  onConfirm,
  onCancel,
}: ReprocessDialogProps) {
  const [stage, setStage] = useState<ReprocessFromStage>("clean");
  const [refreshFacts, setRefreshFacts] = useState(false);
  const option = STAGES.find((s) => s.stage === stage)!;

  // Reset when the dialog closes, not on confirm: a refused request (409)
  // leaves the dialog open with the choice intact.
  useEffect(() => {
    if (!isOpen) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStage("clean");
      setRefreshFacts(false);
    }
  }, [isOpen]);

  return (
    <ConfirmDialog
      isOpen={isOpen}
      title="Reprocess episode"
      confirmLabel={confirmLabel(stage, duration)}
      busy={busy}
      onCancel={onCancel}
      onConfirm={() =>
        onConfirm({
          from_stage: stage,
          refresh_episode_facts: option.facts ? refreshFacts : false,
        })
      }
      message={
        <div className="space-y-3">
          <p>
            Re-run one stage of{" "}
            <span className="font-medium text-ink">{episodeTitle}</span> and
            every stage after it. Readers keep the current transcript and
            summary until the new ones are ready.
          </p>
          <fieldset className="space-y-1">
            <legend className="mb-1 text-xs font-medium text-ink">
              Start from
            </legend>
            {STAGES.map((s) => (
              <label
                key={s.stage}
                className="flex cursor-pointer gap-2 rounded-md p-2 hover:bg-gray-50"
              >
                <input
                  type="radio"
                  name="reprocess-stage"
                  value={s.stage}
                  checked={stage === s.stage}
                  onChange={() => setStage(s.stage)}
                  className="mt-0.5"
                />
                <span>
                  <span className="block font-medium text-ink">{s.label}</span>
                  <span className="block text-xs text-muted">{s.detail}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {option.facts && (
            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={refreshFacts}
                onChange={(e) => setRefreshFacts(e.target.checked)}
                className="mt-0.5"
              />
              <span>Refresh speaker names and guests</span>
            </label>
          )}
          {stage === "transcribe" && (
            <p className="rounded-md bg-amber-50 p-2 text-xs text-amber-900">
              {duration
                ? `This pays for transcribing ${duration} of audio again.`
                : "This pays for transcribing the whole episode again."}
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
        </div>
      }
    />
  );
}
