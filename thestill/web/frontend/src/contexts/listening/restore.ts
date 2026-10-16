// Spec #90 "Restore on load" — which source a freshly mounted player
// restores from.
import type { SessionSnapshot } from './sessionSnapshot'

// Older snapshots may carry an expired media URL; the server path is fresher.
export const SNAPSHOT_MAX_AGE_MS = 30 * 60_000
// Playback resumes automatically only after a reload, not on a return hours later.
export const AUTOPLAY_MAX_AGE_MS = 2 * 60_000

export type RestorePlan =
  | { kind: 'snapshot'; snapshot: SessionSnapshot; autoplay: boolean }
  | { kind: 'latest' }

export function planRestore(snapshot: SessionSnapshot | null, now: number): RestorePlan {
  if (!snapshot) return { kind: 'latest' }
  const age = now - snapshot.savedAt
  if (age < 0 || age >= SNAPSHOT_MAX_AGE_MS) return { kind: 'latest' }
  return { kind: 'snapshot', snapshot, autoplay: snapshot.wasPlaying && age < AUTOPLAY_MAX_AGE_MS }
}
