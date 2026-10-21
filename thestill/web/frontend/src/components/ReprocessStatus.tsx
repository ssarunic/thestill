import type { EpisodeTask, PipelineStage } from "../api/types";
import { useAuth } from "../contexts/AuthContext";
import { ENTITY_BRANCH_STAGES } from "../constants/stages";

// Spec #106 §4 — while a reprocess runs through the user chain (download …
// summarize) the episode stays `summarized`, so the pipeline button stays
// hidden and nothing else on the page reports it. One admin-only line does;
// EntityBranchProgress takes over once the run reaches the entity branch.

const ACTIVE = new Set(["pending", "processing", "retry_scheduled"]);

const STAGE_LABEL: Partial<Record<PipelineStage, string>> = {
  download: "downloading audio",
  downsample: "preparing audio",
  transcribe: "transcribing",
  clean: "cleaning the transcript",
  summarize: "summarizing",
};

export default function ReprocessStatus({
  tasks,
  className = "",
}: {
  tasks: EpisodeTask[];
  className?: string;
}) {
  const { isAdmin } = useAuth();
  if (!isAdmin) return null;

  const task = tasks.find(
    (t) =>
      ACTIVE.has(t.status) &&
      t.metadata?.reprocess &&
      !ENTITY_BRANCH_STAGES.has(t.stage),
  );
  if (!task) return null;

  const what = STAGE_LABEL[task.stage] ?? task.stage;
  const when =
    task.status === "processing"
      ? ""
      : task.status === "retry_scheduled"
        ? " (retrying soon)"
        : " (queued)";
  return (
    <p
      role="status"
      className={`rounded-md bg-indigo-50 px-3 py-2 text-sm text-indigo-900 ${className}`}
    >
      Reprocessing from {task.metadata!.reprocess!.from_stage}: {what}
      {when}. The current version stays up until it finishes.
    </p>
  );
}
