import type { PipelineStage } from '../api/types'

// Canonical lane order: user chain first (download → … → summarize),
// then the spec #28 entity branch (extract-entities → … → reindex).
// Mirrors `STAGE_SUCCESSORS` in thestill/core/queue_manager.py:106.
export const STAGE_ORDER: PipelineStage[] = [
  'download',
  'downsample',
  'transcribe',
  'clean',
  'summarize',
  'extract-entities',
  'resolve-entities',
  'write-corpus',
  'reindex',
]

// Linear ranking used to collapse a per-episode task history to its
// most-progressed stage in the queue viewer's grouped completed feed.
// The entity branch runs in parallel with `summarize`, but for UI
// sorting we treat reindex as "most advanced" so episodes that have
// completed both branches sort last.
export const STAGE_RANK: Record<PipelineStage, number> = {
  download: 0,
  downsample: 1,
  transcribe: 2,
  clean: 3,
  summarize: 4,
  'extract-entities': 5,
  'resolve-entities': 6,
  'write-corpus': 7,
  reindex: 8,
}

// Display labels in the queue viewer (verb form, present tense).
export const STAGE_LABEL: Record<PipelineStage, string> = {
  download: 'Download',
  downsample: 'Downsample',
  transcribe: 'Transcribe',
  clean: 'Clean',
  summarize: 'Summarize',
  'extract-entities': 'Extract entities',
  'resolve-entities': 'Resolve',
  'write-corpus': 'Write corpus',
  reindex: 'Reindex',
}

// Active-voice labels for the per-episode card processing badge.
export const STAGE_LABEL_ACTIVE: Record<PipelineStage, string> = {
  download: 'Downloading',
  downsample: 'Downsampling',
  transcribe: 'Transcribing',
  clean: 'Cleaning',
  summarize: 'Summarizing',
  'extract-entities': 'Extracting entities',
  'resolve-entities': 'Resolving entities',
  'write-corpus': 'Writing corpus',
  reindex: 'Reindexing',
}

// Past-tense / noun-form labels for the failure modal ("Cleaning"
// makes sense alongside "Summary"). Kept distinct from STAGE_LABEL
// because the modal sentence reads "Failed during {label}".
export const STAGE_LABEL_FAILURE: Record<string, string> = {
  download: 'Download',
  downsample: 'Downsample',
  transcribe: 'Transcription',
  clean: 'Cleaning',
  summarize: 'Summary',
  'extract-entities': 'Extracting entities',
  'resolve-entities': 'Resolving',
  'write-corpus': 'Writing corpus',
  reindex: 'Reindexing',
}

// Tailwind colour palette: blue→indigo→purple→amber→green for the
// user chain, rose→pink→fuchsia→violet for the entity branch — visually
// distinct so it's obvious at a glance which branch a task belongs to.
export const STAGE_BADGE_COLOR: Record<PipelineStage, string> = {
  download: 'bg-blue-100 text-blue-700',
  downsample: 'bg-indigo-100 text-indigo-700',
  transcribe: 'bg-purple-100 text-purple-700',
  clean: 'bg-amber-100 text-amber-700',
  summarize: 'bg-green-100 text-green-700',
  'extract-entities': 'bg-rose-100 text-rose-700',
  'resolve-entities': 'bg-pink-100 text-pink-700',
  'write-corpus': 'bg-fuchsia-100 text-fuchsia-700',
  reindex: 'bg-violet-100 text-violet-700',
}

// Lane-accent border colours (queue viewer left rail).
export const STAGE_LANE_ACCENT: Record<PipelineStage, string> = {
  download: 'border-blue-300',
  downsample: 'border-indigo-300',
  transcribe: 'border-purple-300',
  clean: 'border-amber-300',
  summarize: 'border-green-300',
  'extract-entities': 'border-rose-300',
  'resolve-entities': 'border-pink-300',
  'write-corpus': 'border-fuchsia-300',
  reindex: 'border-violet-300',
}

// Solid button colours used by PipelineActionButton when triggering a
// stage manually (deeper shades than the badge palette).
export const STAGE_BUTTON_COLOR: Record<PipelineStage, string> = {
  download: 'bg-blue-600 hover:bg-blue-700',
  downsample: 'bg-indigo-600 hover:bg-indigo-700',
  transcribe: 'bg-purple-600 hover:bg-purple-700',
  clean: 'bg-amber-600 hover:bg-amber-700',
  summarize: 'bg-green-600 hover:bg-green-700',
  'extract-entities': 'bg-rose-600 hover:bg-rose-700',
  'resolve-entities': 'bg-pink-600 hover:bg-pink-700',
  'write-corpus': 'bg-fuchsia-600 hover:bg-fuchsia-700',
  reindex: 'bg-violet-600 hover:bg-violet-700',
}
