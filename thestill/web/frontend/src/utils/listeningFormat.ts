// Spec #90 — how listening progress reads on surfaces.
import type { ListeningSummary } from '../api/types'
import { resumablePosition } from '../contexts/listening/resume'
import { formatMinutes } from './episodeFormat'

export type ListeningState = 'none' | 'in_progress' | 'played'

export function listeningState(listening: ListeningSummary | null | undefined): ListeningState {
  if (listening?.completed_at) return 'played'
  return resumablePosition(listening) === null ? 'none' : 'in_progress'
}

// The row's own duration first (engine-reported, logical); the feed's
// often-missing or rounded duration only as a fallback.
function totalSeconds(listening: ListeningSummary, fallbackDuration: number | null | undefined): number | null {
  const total = listening.duration_seconds ?? fallbackDuration ?? null
  return total && total > 0 ? total : null
}

/** ``32 min left`` for an episode in progress with a known duration, else null. */
export function timeLeftLabel(
  listening: ListeningSummary | null | undefined,
  fallbackDuration?: number | null,
): string | null {
  if (!listening || listeningState(listening) !== 'in_progress') return null
  const total = totalSeconds(listening, fallbackDuration)
  if (total === null) return null
  const minutes = formatMinutes(Math.max(1, total - listening.position_seconds))
  return minutes ? `${minutes} left` : null
}

/** Share of the episode listened (0–1) while in progress, else null. */
export function progressFraction(
  listening: ListeningSummary | null | undefined,
  fallbackDuration?: number | null,
): number | null {
  if (!listening || listeningState(listening) !== 'in_progress') return null
  const total = totalSeconds(listening, fallbackDuration)
  if (total === null) return null
  return Math.min(1, Math.max(0, listening.position_seconds / total))
}
