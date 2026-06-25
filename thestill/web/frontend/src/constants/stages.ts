import type { PipelineStage } from '../api/types'

// Spec #28/#46/#47 entity-branch stage membership — mirrors the
// ``ENTITY_BRANCH_STAGES`` frozenset in
// thestill/core/queue_manager.py. Used by the FailedTasks page to
// classify DLQ entries client-side without re-querying.
export const ENTITY_BRANCH_STAGES: ReadonlySet<PipelineStage> = new Set([
  'extract-entities',
  'resolve-entities',
  'reindex',
  'rebuild-cooccurrences',
  'compute-related',
  'enrich-entities',
])

// Spec #48 podcast-scoped (feed) stage membership — mirrors
// ``_FEED_SCOPED_STAGES`` in thestill/core/queue_manager.py. These tasks
// target a podcast, not an episode, and form their own DLQ branch.
export const FEED_SCOPED_STAGES: ReadonlySet<PipelineStage> = new Set([
  'refresh-feed',
])

// Canonical lane order: the feed producer (refresh-feed) first, then the
// user chain (download → … → summarize), then the entity branch
// (extract-entities → … → enrich-entities). Mirrors the backend
// ``TaskStage`` ordering in thestill/core/queue_manager.py.
export const STAGE_ORDER: PipelineStage[] = [
  'refresh-feed',
  'download',
  'downsample',
  'transcribe',
  'clean',
  'summarize',
  'extract-entities',
  'resolve-entities',
  'reindex',
  'rebuild-cooccurrences',
  'compute-related',
  'enrich-entities',
]

// Linear ranking used to collapse a per-episode task history to its
// most-progressed stage in the queue viewer's grouped completed feed.
// The entity branch runs in parallel with `summarize`, but for UI
// sorting we treat the terminal entity stage as "most advanced" so
// episodes that completed both branches sort last. ``refresh-feed`` is
// podcast-scoped (never in an episode's history) so it ranks below 0.
export const STAGE_RANK: Record<PipelineStage, number> = {
  'refresh-feed': -1,
  download: 0,
  downsample: 1,
  transcribe: 2,
  clean: 3,
  summarize: 4,
  'extract-entities': 5,
  'resolve-entities': 6,
  reindex: 7,
  'rebuild-cooccurrences': 8,
  'compute-related': 9,
  'enrich-entities': 10,
}

// Display labels in the queue viewer (verb form, present tense).
export const STAGE_LABEL: Record<PipelineStage, string> = {
  'refresh-feed': 'Refresh feed',
  download: 'Download',
  downsample: 'Downsample',
  transcribe: 'Transcribe',
  clean: 'Clean',
  summarize: 'Summarize',
  'extract-entities': 'Extract entities',
  'resolve-entities': 'Resolve',
  reindex: 'Index chunks',
  'rebuild-cooccurrences': 'Rebuild co-occurrences',
  'compute-related': 'Compute related',
  'enrich-entities': 'Enrich entities',
}

// Active-voice labels for the per-episode card processing badge.
export const STAGE_LABEL_ACTIVE: Record<PipelineStage, string> = {
  'refresh-feed': 'Refreshing feed',
  download: 'Downloading',
  downsample: 'Downsampling',
  transcribe: 'Transcribing',
  clean: 'Cleaning',
  summarize: 'Summarizing',
  'extract-entities': 'Extracting entities',
  'resolve-entities': 'Resolving entities',
  reindex: 'Indexing chunks',
  'rebuild-cooccurrences': 'Rebuilding co-occurrences',
  'compute-related': 'Computing related',
  'enrich-entities': 'Enriching entities',
}

// Past-tense / noun-form labels for the failure modal ("Cleaning"
// makes sense alongside "Summary"). Kept distinct from STAGE_LABEL
// because the modal sentence reads "Failed during {label}".
export const STAGE_LABEL_FAILURE: Record<string, string> = {
  'refresh-feed': 'Feed refresh',
  download: 'Download',
  downsample: 'Downsample',
  transcribe: 'Transcription',
  clean: 'Cleaning',
  summarize: 'Summary',
  'extract-entities': 'Extracting entities',
  'resolve-entities': 'Resolving',
  reindex: 'Indexing chunks',
  'rebuild-cooccurrences': 'Co-occurrence rebuild',
  'compute-related': 'Related episodes',
  'enrich-entities': 'Entity enrichment',
}

// Tailwind colour palette: slate for the feed producer, blue→…→green for
// the user chain, rose→…→teal for the entity branch — visually distinct
// so it's obvious at a glance which branch a task belongs to.
export const STAGE_BADGE_COLOR: Record<PipelineStage, string> = {
  'refresh-feed': 'bg-slate-100 text-slate-700',
  download: 'bg-blue-100 text-blue-700',
  downsample: 'bg-indigo-100 text-indigo-700',
  transcribe: 'bg-purple-100 text-purple-700',
  clean: 'bg-amber-100 text-amber-700',
  summarize: 'bg-green-100 text-green-700',
  'extract-entities': 'bg-rose-100 text-rose-700',
  'resolve-entities': 'bg-pink-100 text-pink-700',
  reindex: 'bg-violet-100 text-violet-700',
  'rebuild-cooccurrences': 'bg-fuchsia-100 text-fuchsia-700',
  'compute-related': 'bg-cyan-100 text-cyan-700',
  'enrich-entities': 'bg-teal-100 text-teal-700',
}

// Lane-accent border colours (queue viewer left rail).
export const STAGE_LANE_ACCENT: Record<PipelineStage, string> = {
  'refresh-feed': 'border-slate-300',
  download: 'border-blue-300',
  downsample: 'border-indigo-300',
  transcribe: 'border-purple-300',
  clean: 'border-amber-300',
  summarize: 'border-green-300',
  'extract-entities': 'border-rose-300',
  'resolve-entities': 'border-pink-300',
  reindex: 'border-violet-300',
  'rebuild-cooccurrences': 'border-fuchsia-300',
  'compute-related': 'border-cyan-300',
  'enrich-entities': 'border-teal-300',
}

// Solid button colours used by PipelineActionButton when triggering a
// stage manually (deeper shades than the badge palette).
export const STAGE_BUTTON_COLOR: Record<PipelineStage, string> = {
  'refresh-feed': 'bg-slate-600 hover:bg-slate-700',
  download: 'bg-blue-600 hover:bg-blue-700',
  downsample: 'bg-indigo-600 hover:bg-indigo-700',
  transcribe: 'bg-purple-600 hover:bg-purple-700',
  clean: 'bg-amber-600 hover:bg-amber-700',
  summarize: 'bg-green-600 hover:bg-green-700',
  'extract-entities': 'bg-rose-600 hover:bg-rose-700',
  'resolve-entities': 'bg-pink-600 hover:bg-pink-700',
  reindex: 'bg-violet-600 hover:bg-violet-700',
  'rebuild-cooccurrences': 'bg-fuchsia-600 hover:bg-fuchsia-700',
  'compute-related': 'bg-cyan-600 hover:bg-cyan-700',
  'enrich-entities': 'bg-teal-600 hover:bg-teal-700',
}
