// Spec #116 §6.1 / §6.2 — the ad breaks of the transcribed copy. The reader
// draws each as one row; the player offers Skip ahead inside one.
import type { AnnotatedTranscriptDump, SegmentKind } from '../api/types'
import type { AdBreak } from '../contexts/playback-engine/timeline-map'

/**
 * Each ad break as `[first, last]` segment indexes: a maximal run of
 * consecutive `ad_break` segments. Filler inside a run does not split it.
 */
export function adBreakRunBounds(segments: ReadonlyArray<{ kind: SegmentKind }>): Array<[number, number]> {
  const runs: Array<[number, number]> = []
  let i = 0
  while (i < segments.length) {
    if (segments[i].kind !== 'ad_break') {
      i += 1
      continue
    }
    let last = i
    for (let j = i + 1; j < segments.length; j += 1) {
      if (segments[j].kind === 'ad_break') last = j
      else if (segments[j].kind !== 'filler') break
    }
    runs.push([i, last])
    i = last + 1
  }
  return runs
}

/**
 * The breaks Skip ahead may jump over, in playback seconds: those with
 * episode content after them. A break that closes the episode has nothing
 * to skip ahead to.
 */
export function skippableAdBreaks(transcript: AnnotatedTranscriptDump): AdBreak[] {
  const { segments } = transcript
  const offset = transcript.playback_time_offset_seconds ?? 0
  return adBreakRunBounds(segments)
    .filter(([, last]) => segments.slice(last + 1).some((s) => s.kind === 'content'))
    .map(([first, last]) => ({ startS: segments[first].start + offset, endS: segments[last].end + offset }))
}
