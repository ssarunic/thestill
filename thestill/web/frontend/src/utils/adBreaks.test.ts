import { describe, expect, it } from 'vitest'
import type { AnnotatedTranscriptDump } from '../api/types'
import { adBreakRunBounds, skippableAdBreaks } from './adBreaks'

const seg = (start: number, end: number, kind: string) => ({ start, end, kind })
const dump = (segments: ReturnType<typeof seg>[], offset = 0) =>
  ({ segments, playback_time_offset_seconds: offset }) as unknown as AnnotatedTranscriptDump

describe('ad breaks of the transcribed copy (spec #116 §6.1, §6.2)', () => {
  it('runs consecutive ad segments together; filler inside does not split them', () => {
    const segments = [
      seg(0, 10, 'content'),
      seg(10, 20, 'ad_break'),
      seg(20, 21, 'filler'),
      seg(21, 30, 'ad_break'),
      seg(30, 40, 'content'),
      seg(40, 50, 'ad_break'),
    ] as never[]
    expect(adBreakRunBounds(segments)).toEqual([
      [1, 3],
      [5, 5],
    ])
  })

  it('offers only breaks with content after them, in playback seconds', () => {
    const transcript = dump(
      [seg(0, 15, 'ad_break'), seg(15, 40, 'content'), seg(40, 50, 'ad_break'), seg(50, 55, 'outro')],
      2,
    )
    expect(skippableAdBreaks(transcript)).toEqual([{ startS: 2, endS: 17 }])
  })
})
