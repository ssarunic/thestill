// Spec #90 "Resume on play" — where a fresh play() of an episode starts.
import type { ListeningSummary } from '../../api/types'

// A stored position this close to the top is not worth resuming.
export const MIN_RESUME_SECONDS = 2

export interface ResumeDecision {
  // Engine-time seek for the new source, or null to start at the top.
  seekTo: number | null
  // The position is unknown (no payload carried it): fetch it in parallel
  // and apply it late (PlayerPort.applyLateSeek → NativeEngine.setPendingSeek).
  fetchLate: boolean
}

/** The resumable logical position of a cached summary, or null. */
export function resumablePosition(summary: ListeningSummary | null | undefined): number | null {
  if (!summary || summary.completed_at) return null
  return summary.position_seconds >= MIN_RESUME_SECONDS ? summary.position_seconds : null
}

/**
 * An explicit `startAt` always wins (transcript segment, summary citation,
 * entity mention); `restart` ("Play from beginning") starts at the top; a
 * completed episode starts at the top; otherwise the stored logical position
 * is mapped onto the chosen asset's timeline by adding its offset.
 */
export function resolveResume(args: {
  startAt?: number
  restart?: boolean
  cached: ListeningSummary | null | undefined
  offset: number
}): ResumeDecision {
  const { startAt, restart, cached, offset } = args
  if (startAt !== undefined && Number.isFinite(startAt)) return { seekTo: Math.max(0, startAt), fetchLate: false }
  if (restart) return { seekTo: null, fetchLate: false }
  if (cached === undefined) return { seekTo: null, fetchLate: true }
  const position = resumablePosition(cached)
  return { seekTo: position === null ? null : position + offset, fetchLate: false }
}
