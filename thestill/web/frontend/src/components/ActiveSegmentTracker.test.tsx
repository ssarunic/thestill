import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import type { AnnotatedSegment, SegmentKind } from '../api/types'

// Spec #69 Phase 7.2 — the tracker is the only 4Hz clock subscriber; it
// must report the active segment id upward ONLY when it changes, so the
// 10k-row viewer re-renders per segment transition instead of per tick.

let mockTime = 0
vi.mock('../contexts/PlayerContext', () => ({
  usePlayerTime: () => mockTime,
  usePlayer: () => ({ track: null, getCurrentTime: () => mockTime }),
}))

import { ActiveSegmentTracker } from './SegmentedTranscriptViewer'

function seg(overrides: Partial<AnnotatedSegment> & { id: number }): AnnotatedSegment {
  return {
    id: overrides.id,
    start: overrides.start ?? 0,
    end: overrides.end ?? 1,
    speaker: overrides.speaker ?? 'Host',
    text: overrides.text ?? 'content text',
    kind: (overrides.kind ?? 'content') as SegmentKind,
    sponsor: overrides.sponsor ?? null,
    source_segment_ids: overrides.source_segment_ids ?? [overrides.id],
    source_word_span: overrides.source_word_span ?? null,
    user_segment_id: overrides.user_segment_id ?? null,
    metadata: overrides.metadata ?? {},
  }
}

const SEGMENTS = [
  seg({ id: 1, start: 0, end: 10 }),
  seg({ id: 2, start: 10, end: 20 }),
  seg({ id: 3, start: 20, end: 30 }),
]

function renderTracker(renderedIdSet: Set<number>) {
  const onActiveChange = vi.fn()
  // A fresh element per render — reusing one element reference would let
  // React bail out of re-rendering on identical element identity, which
  // would make the ticks below no-ops.
  const build = () => (
    <ActiveSegmentTracker
      segments={SEGMENTS}
      offset={0}
      renderedIdSet={renderedIdSet}
      onActiveChange={onActiveChange}
    />
  )
  const utils = render(build())
  const tick = (t: number) => {
    mockTime = t
    utils.rerender(build())
  }
  return { onActiveChange, tick }
}

describe('ActiveSegmentTracker', () => {
  beforeEach(() => {
    mockTime = 0
  })

  it('reports the active id once per transition, not once per tick', () => {
    const { onActiveChange, tick } = renderTracker(new Set([1, 2, 3]))
    expect(onActiveChange).toHaveBeenLastCalledWith(1)
    const callsAfterMount = onActiveChange.mock.calls.length

    // Four ticks inside the same segment: no further upward reports.
    tick(2)
    tick(4)
    tick(6)
    tick(9.9)
    expect(onActiveChange.mock.calls.length).toBe(callsAfterMount)

    // Crossing into segment 2 reports exactly once.
    tick(11)
    expect(onActiveChange).toHaveBeenLastCalledWith(2)
    expect(onActiveChange.mock.calls.length).toBe(callsAfterMount + 1)
  })

  it('walks back to the nearest visible segment when the active one is filtered out', () => {
    // Segment 2 is filtered from view (entity filter / hidden kind).
    const { onActiveChange, tick } = renderTracker(new Set([1, 3]))
    tick(15) // inside segment 2, which is not visible
    expect(onActiveChange).toHaveBeenLastCalledWith(1)
    tick(25)
    expect(onActiveChange).toHaveBeenLastCalledWith(3)
  })

  it('reports null before the first segment starts', () => {
    const { onActiveChange, tick } = renderTracker(new Set([1, 2, 3]))
    tick(-5)
    expect(onActiveChange).toHaveBeenLastCalledWith(null)
  })
})
