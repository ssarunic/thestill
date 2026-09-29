import type { Episode } from '../api/types'

// Pipeline progress as the user perceives it. Derived from episode state +
// failure flags so two users sharing an imported episode see consistent
// progress without storing per-user pipeline state.
export type ProgressKind = 'failed' | 'processing' | 'ready'

export interface ProgressStatus {
  kind: ProgressKind
  label: string
}

export function deriveProgress(episode: Pick<Episode, 'state' | 'is_failed'>): ProgressStatus {
  if (episode.is_failed) {
    return { kind: 'failed', label: 'Failed' }
  }
  switch (episode.state) {
    case 'discovered':
      return { kind: 'processing', label: 'Downloading…' }
    case 'downloaded':
    case 'downsampled':
      return { kind: 'processing', label: 'Transcribing…' }
    case 'transcribed':
      return { kind: 'processing', label: 'Cleaning…' }
    case 'cleaned':
      return { kind: 'processing', label: 'Summarising…' }
    case 'summarized':
      return { kind: 'ready', label: 'Ready' }
    default:
      return { kind: 'processing', label: 'Processing…' }
  }
}

// Spec #88.
export const SAVED_VIEW_HREF = '/inbox?view=saved'

// "11 May", or "11 May 2025" outside the current year.
export function formatDeliveredDate(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    ...(date.getFullYear() === new Date().getFullYear() ? {} : { year: 'numeric' }),
  })
}
