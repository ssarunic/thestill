// Spec #38 karaoke wipe — viewer integration tests.
//
// These mock ``PlayerContext`` directly so we can pin a track + currentTime
// that puts the viewer in "isCurrentEpisode + active segment" state, which
// is what unlocks the karaoke render path. The main viewer test file uses
// the real PlayerProvider, but that path can't easily be coerced into
// having an active segment (no real audio plays in jsdom), so karaoke gets
// its own file with mocked context to test the prop plumbing.

import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type {
  AnnotatedSegment,
  AnnotatedTranscriptDump,
  KaraokeWordsByEpisode,
  WordTimestamp,
} from '../api/types'
import SegmentedTranscriptViewer from './SegmentedTranscriptViewer'
import { ToastProvider } from './Toast'

vi.mock('../contexts/PlayerContext', () => {
  return {
    usePlayer: () => ({
      track: { episodeId: 'ep1', podcastSlug: 'p', episodeSlug: 'e', title: '', audioUrl: '' },
      isPlaying: true,
      isLoading: false,
      duration: 0,
      playbackRate: 1,
      play: () => undefined,
      pause: () => undefined,
      resume: () => undefined,
      toggle: () => undefined,
      seek: () => undefined,
      skip: () => undefined,
      setRate: () => undefined,
      stop: () => undefined,
      isCurrent: (id: string) => id === 'ep1',
      getCurrentTime: () => 1.2,
    }),
    usePlayerTime: () => 1.2,
    // Toast etc. don't need this, but providers wrapping the viewer might
    // still import it — keep the API surface complete.
    PlayerProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  }
})

function seg(overrides: Partial<AnnotatedSegment> & { id: number }): AnnotatedSegment {
  return {
    id: overrides.id,
    start: overrides.start ?? 0,
    end: overrides.end ?? 1,
    speaker: overrides.speaker ?? 'Host',
    text: overrides.text ?? 'placeholder cleaned text',
    kind: overrides.kind ?? 'content',
    sponsor: null,
    source_segment_ids: [overrides.id],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
  }
}

function transcript(): AnnotatedTranscriptDump {
  return {
    episode_id: 'ep1',
    segments: [
      seg({ id: 1, start: 0, end: 2, text: 'placeholder cleaned text' }),
      seg({ id: 2, start: 2, end: 4, text: 'second segment cleaned' }),
    ],
    playback_time_offset_seconds: 0,
    algorithm_version: 'v1',
    transcript_source_duration_s: null,
  }
}

function words(): KaraokeWordsByEpisode {
  // Raw words for segment 1; mid-segment activeTime=1.2 falls inside "raw"
  const segOne: WordTimestamp[] = [
    { w: 'Hello', s: 0.0, e: 0.5 },
    { w: 'raw', s: 0.6, e: 1.5 },
    { w: 'world.', s: 1.6, e: 1.9 },
  ]
  return {
    episodeId: 'ep1',
    offset: 0,
    wordsBySegmentId: new Map<number, WordTimestamp[]>([[1, segOne]]),
  }
}

function renderViewer(props: Parameters<typeof SegmentedTranscriptViewer>[0]) {
  return render(
    <ToastProvider>
      <SegmentedTranscriptViewer {...props} />
    </ToastProvider>,
  )
}

describe('SegmentedTranscriptViewer — karaoke integration', () => {
  it('renders cleaned text when karaokeEnabled is false even if words are provided', () => {
    const { container } = renderViewer({
      transcript: transcript(),
      episodeId: 'ep1',
      karaokeEnabled: false,
      karaokeWords: words(),
    })
    expect(container.querySelectorAll('[data-karaoke-word]').length).toBe(0)
    expect(container.textContent).toMatch(/placeholder cleaned text/)
  })

  it('falls back to cleaned text when karaokeWords is null (404 case)', () => {
    const { container } = renderViewer({
      transcript: transcript(),
      episodeId: 'ep1',
      karaokeEnabled: true,
      karaokeWords: null,
    })
    expect(container.querySelectorAll('[data-karaoke-word]').length).toBe(0)
    expect(container.textContent).toMatch(/placeholder cleaned text/)
  })

  it('renders KaraokeWord spans for the active segment when karaoke is on', () => {
    const { container } = renderViewer({
      transcript: transcript(),
      episodeId: 'ep1',
      karaokeEnabled: true,
      karaokeWords: words(),
    })
    const wordSpans = container.querySelectorAll('[data-karaoke-word]')
    // Three raw words for segment 1.
    expect(wordSpans.length).toBe(3)
    // The raw word text replaces the cleaned text for the active segment.
    expect(container.textContent).toMatch(/Hello raw world\./)
    expect(container.textContent).not.toMatch(/placeholder cleaned text/)
  })

  it('marks exactly the active word with aria-current at the audio cursor', () => {
    // currentTime=1.2 falls inside the middle word [0.6, 1.5].
    const { container } = renderViewer({
      transcript: transcript(),
      episodeId: 'ep1',
      karaokeEnabled: true,
      karaokeWords: words(),
    })
    const active = container.querySelectorAll('[data-karaoke-word][aria-current="true"]')
    expect(active.length).toBe(1)
    expect(active[0].textContent).toBe('raw')
  })

  it('inactive segments stay on cleaned text even when karaoke is on', () => {
    const { container } = renderViewer({
      transcript: transcript(),
      episodeId: 'ep1',
      karaokeEnabled: true,
      karaokeWords: words(),
    })
    // The other segment carries no entry in the words map and is not the
    // active segment — its cleaned text remains.
    expect(container.textContent).toMatch(/second segment cleaned/)
  })

  it('falls back to cleaned text for a segment with no karaoke words entry', () => {
    // Karaoke is on, but the words map has nothing for the active segment.
    const w = words()
    w.wordsBySegmentId = new Map()
    const { container } = renderViewer({
      transcript: transcript(),
      episodeId: 'ep1',
      karaokeEnabled: true,
      karaokeWords: w,
    })
    expect(container.querySelectorAll('[data-karaoke-word]').length).toBe(0)
    expect(container.textContent).toMatch(/placeholder cleaned text/)
  })

  it('renders the Karaoke chip when onKaraokeToggle is provided', () => {
    const onToggle = vi.fn()
    const { getByRole } = renderViewer({
      transcript: transcript(),
      episodeId: 'ep1',
      karaokeChipChecked: false,
      karaokeChipDisabled: false,
      onKaraokeToggle: onToggle,
    })
    const chip = getByRole('checkbox', { name: /Karaoke/ })
    expect(chip).toBeInTheDocument()
    expect((chip as HTMLInputElement).disabled).toBe(false)
    expect((chip as HTMLInputElement).checked).toBe(false)
  })

  it('hides the Karaoke chip when onKaraokeToggle is not provided', () => {
    const { queryByRole } = renderViewer({
      transcript: transcript(),
      episodeId: 'ep1',
    })
    expect(queryByRole('checkbox', { name: /Karaoke/ })).toBeNull()
  })

  it('renders the chip disabled with a tooltip when karaokeChipDisabled is true', () => {
    const { getByRole } = renderViewer({
      transcript: transcript(),
      episodeId: 'ep1',
      karaokeChipChecked: true,
      karaokeChipDisabled: true,
      onKaraokeToggle: () => undefined,
    })
    const chip = getByRole('checkbox', { name: /Karaoke/ }) as HTMLInputElement
    expect(chip.disabled).toBe(true)
    // Tooltip text lives on the surrounding <label>; find it via the
    // chip's parent. JSDOM resolves the title attribute on hover, but we
    // can verify the markup carries it.
    const label = chip.closest('label')
    expect(label?.getAttribute('title')).toMatch(/No word timestamps/i)
  })

  it('fires onKaraokeToggle when the chip is clicked', () => {
    const onToggle = vi.fn()
    const { getByRole } = renderViewer({
      transcript: transcript(),
      episodeId: 'ep1',
      karaokeChipChecked: false,
      karaokeChipDisabled: false,
      onKaraokeToggle: onToggle,
    })
    const chip = getByRole('checkbox', { name: /Karaoke/ }) as HTMLInputElement
    chip.click()
    expect(onToggle).toHaveBeenCalledTimes(1)
  })
})
