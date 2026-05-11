/**
 * Binary-search the latest item whose start (plus offset) is ≤ `currentTime`.
 *
 * Shared core for `findActiveSegmentIndex` (operating on `AnnotatedSegment`
 * with `start`/`end` field names + a 0.75s tolerance) and `findActiveWordIndex`
 * (operating on `WordTimestamp` with `s`/`e` field names + a 0.15s tolerance).
 *
 * Returns -1 when the list is empty, when `currentTime` is before the first
 * item, or when `currentTime` is past `getEnd(item) + offset + tolerance` of
 * the latest match — the tolerance is what keeps the highlight from lingering
 * across a trimmed gap, then gives up when the gap exceeds the slop.
 */
export function findActiveIndex<T>(
  items: ReadonlyArray<T>,
  getStart: (item: T) => number,
  getEnd: (item: T) => number,
  currentTime: number,
  offset: number,
  tolerance: number,
): number {
  if (items.length === 0) return -1
  if (!Number.isFinite(currentTime)) return -1

  let lo = 0
  let hi = items.length - 1
  let found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const start = getStart(items[mid]) + offset
    if (start <= currentTime) {
      found = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  if (found === -1) return -1
  const end = getEnd(items[found]) + offset + tolerance
  if (currentTime > end) return -1
  return found
}
