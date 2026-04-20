import type { AnnotatedSegment } from '../api/types'

/**
 * Binary-search the latest segment whose `start + offset` is ≤ `currentTime`.
 *
 * Returns -1 when `currentTime` is before the first segment's start or when
 * the list is empty. The caller decides what "too far past" looks like:
 * `tolerance` is an optional slop added to the matched segment's `end` — if
 * `currentTime` is beyond `end + tolerance + offset` the helper returns -1,
 * which keeps the highlight from lingering on a segment the audio has long
 * since left (e.g. a trimmed gap).
 */
export function findActiveSegmentIndex(
  segments: ReadonlyArray<AnnotatedSegment>,
  currentTime: number,
  offset: number,
  tolerance: number = 0.75,
): number {
  if (segments.length === 0) return -1
  if (!Number.isFinite(currentTime)) return -1

  let lo = 0
  let hi = segments.length - 1
  let found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const start = segments[mid].start + offset
    if (start <= currentTime) {
      found = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  if (found === -1) return -1
  const end = segments[found].end + offset + tolerance
  if (currentTime > end) return -1
  return found
}
