import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import type { AnnotatedSegment } from '../api/types'
import { findActiveSegmentIndex } from '../utils/transcriptSearch'
import { deepLinkSeconds } from './useDeepLinkSeek'

// History entries (`location.key`) whose `?t=` has already been turned into a
// scroll target. A Back/Forward to one of them is a return, and the saved
// reading position (useReadingPosition) must win — so it is skipped here.
// Module-level for the same reason useReadingPosition's `seenEntries` is:
// the reader remounts across page/overlay hosts within one entry.
const handledEntries = new Set<string>()

/** Test seam. */
export function __resetDeepLinkScrollForTests(): void {
  handledEntries.clear()
}

/**
 * The segment a deep-linked moment lands on: the one under `seconds`, or,
 * when it falls in a gap between segments (or past the last one), the last
 * segment that had started. -1 when the moment is before the first segment.
 */
export function deepLinkSegmentIndex(
  segments: ReadonlyArray<AnnotatedSegment>,
  seconds: number,
  offset: number,
): number {
  const idx = findActiveSegmentIndex(segments, seconds, offset)
  if (idx >= 0) return idx
  for (let i = segments.length - 1; i >= 0; i--) {
    if (segments[i].start + offset <= seconds) return i
  }
  return -1
}

interface Options {
  episodeId: string | undefined
  segments: ReadonlyArray<AnnotatedSegment> | null
  /** `playback_time_offset_seconds` of the transcript (engine time = segment time + offset). */
  offset: number
  onTarget: (segmentId: number) => void
}

/**
 * Spec #72 §Deep link — resolve `?t=<seconds>` (or `#t=`) to the transcript segment under
 * that moment and hand it to the reader's scroll-target mechanism, once per
 * history entry, on fresh entries only. Independent of the follow toggle
 * (useDeepLinkSeek only seeks; scrolling used to depend on follow being on).
 * Waits for segments to be present so the target is resolvable.
 */
export function useDeepLinkScrollTarget({ episodeId, segments, offset, onTarget }: Options): void {
  const location = useLocation()
  const seconds = deepLinkSeconds(location.search, location.hash)

  useEffect(() => {
    if (!episodeId || !segments || segments.length === 0 || seconds === null) return
    if (handledEntries.has(location.key)) return
    handledEntries.add(location.key)
    const idx = deepLinkSegmentIndex(segments, seconds, offset)
    if (idx >= 0) onTarget(segments[idx].id)
  }, [episodeId, segments, offset, seconds, location.key, onTarget])
}
