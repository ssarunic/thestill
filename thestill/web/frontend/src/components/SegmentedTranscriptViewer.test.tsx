import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  AnnotatedSegment,
  AnnotatedTranscriptDump,
  SegmentKind,
} from '../api/types'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { EpisodeEntity, MentionLite } from '../api/types'
import { PlayerProvider } from '../contexts/PlayerContext'
import SegmentedTranscriptViewer from './SegmentedTranscriptViewer'
import { __resetDeepLinkSeekForTests } from '../hooks/useDeepLinkSeek'
import { ToastProvider } from './Toast'

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

function makeTranscript(segments: AnnotatedSegment[]): AnnotatedTranscriptDump {
  return {
    episode_id: 'ep1',
    segments,
    playback_time_offset_seconds: 0,
    algorithm_version: 'v1',
    transcript_source_duration_s: null,
    cleaning: null,
  }
}

function renderViewer(props: Parameters<typeof SegmentedTranscriptViewer>[0]) {
  return render(
    <ToastProvider>
      <PlayerProvider>
        <SegmentedTranscriptViewer {...props} />
      </PlayerProvider>
    </ToastProvider>,
  )
}

const STORAGE_KEY = 'thestill:transcriptViewer:hiddenKinds'

function defaultTranscript(): AnnotatedTranscriptDump {
  return makeTranscript([
    seg({ id: 1, start: 0, end: 10, speaker: 'Alice', text: 'First segment' }),
    seg({
      id: 2,
      start: 10,
      end: 20,
      speaker: 'Bob',
      text: 'Second segment about shipping',
    }),
    seg({
      id: 3,
      start: 20,
      end: 25,
      speaker: 'Alice',
      text: 'um filler words',
      kind: 'filler',
    }),
  ])
}

describe('SegmentedTranscriptViewer', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  describe('filler + playback integration', () => {
    it('renders every non-filler segment by default', () => {
      renderViewer({ transcript: defaultTranscript() })
      expect(screen.getByText('First segment')).toBeInTheDocument()
      expect(screen.getByText(/Second segment about shipping/)).toBeInTheDocument()
      expect(screen.queryByText(/um filler words/)).toBeNull()
    })

    it('reveals filler segments when Show filler is toggled on', () => {
      renderViewer({ transcript: defaultTranscript() })
      fireEvent.click(screen.getByRole('checkbox', { name: /Show filler/ }))
      expect(screen.getByText(/um filler words/)).toBeInTheDocument()
      expect(window.localStorage.getItem('thestill:transcript:showFiller')).toBe('true')
    })

    it('does not expose seek affordance when no handler is provided', () => {
      renderViewer({ transcript: defaultTranscript() })
      expect(screen.queryByRole('button', { name: /Seek to/ })).toBeNull()
    })

    it('fires onSeekRequest with absolute seconds when a segment is clicked', () => {
      const onSeek = vi.fn()
      const data = defaultTranscript()
      data.playback_time_offset_seconds = 100
      renderViewer({ transcript: data, onSeekRequest: onSeek })
      fireEvent.click(screen.getByRole('button', { name: /Seek to 01:50 — Bob/ }))
      expect(onSeek).toHaveBeenCalledWith(110)
    })

    describe('a link to a moment (#t=)', () => {
      const setActivation = (hasBeenActive: boolean | null) =>
        Object.defineProperty(window.navigator, 'userActivation', {
          configurable: true,
          value: hasBeenActive === null ? undefined : { hasBeenActive, isActive: hasBeenActive },
        })
      const openAt = (url: string) => window.history.replaceState(null, '', url)

      beforeEach(() => __resetDeepLinkSeekForTests())
      afterEach(() => {
        setActivation(null)
        openAt('/')
      })

      it('opened cold: marks the passage and offers Play from, without starting audio', () => {
        setActivation(false)
        openAt('/podcasts/show/episodes/ep#t=12')
        const onSeek = vi.fn()
        renderViewer({ transcript: defaultTranscript(), episodeId: 'ep1', onSeekRequest: onSeek })

        expect(onSeek).not.toHaveBeenCalled()
        expect(screen.getByTestId('segment-content-2')).toHaveAttribute('data-linked', 'true')
        expect(screen.getByTestId('segment-content-1')).not.toHaveAttribute('data-linked')

        fireEvent.click(screen.getByRole('button', { name: 'Play from 00:12' }))
        expect(onSeek).toHaveBeenCalledTimes(1)
        expect(onSeek).toHaveBeenCalledWith(12)
        expect(screen.queryByTestId('linked-moment')).toBeNull()
        expect(screen.getByTestId('segment-content-2')).not.toHaveAttribute('data-linked')
      })

      it('followed inside the app: plays the moment, and keeps the offer in case the browser refuses', () => {
        setActivation(true)
        openAt('/podcasts/show/episodes/ep?t=12')
        const onSeek = vi.fn()
        renderViewer({ transcript: defaultTranscript(), episodeId: 'ep1', onSeekRequest: onSeek })
        expect(onSeek).toHaveBeenCalledWith(12)
        // The stubbed handler starts no audio, as a browser refusing would.
        expect(screen.getByRole('button', { name: 'Play from 00:12' })).toBeInTheDocument()
      })

      it('does not seek again on a return to the same history entry, but keeps an untaken offer', () => {
        setActivation(true)
        openAt('/podcasts/show/episodes/ep?t=12')
        const onSeek = vi.fn()
        const props = { transcript: defaultTranscript(), episodeId: 'ep1', onSeekRequest: onSeek, locationKey: 'entry-a' }
        const first = renderViewer(props)
        expect(onSeek).toHaveBeenCalledTimes(1)
        first.unmount()

        // Back to the entry: the viewer mounts again with the same key.
        const second = renderViewer(props)
        expect(onSeek).toHaveBeenCalledTimes(1)
        fireEvent.click(screen.getByRole('button', { name: 'Play from 00:12' }))
        expect(onSeek).toHaveBeenCalledTimes(2)
        second.unmount()

        // Taken up: a further return offers nothing.
        renderViewer(props)
        expect(onSeek).toHaveBeenCalledTimes(2)
        expect(screen.queryByTestId('linked-moment')).toBeNull()
      })

      it('acts again for a new history entry on the same episode', () => {
        setActivation(true)
        openAt('/podcasts/show/episodes/ep?t=12')
        const onSeek = vi.fn()
        const transcript = defaultTranscript()
        const wrap = (locationKey: string) => (
          <ToastProvider>
            <PlayerProvider>
              <SegmentedTranscriptViewer transcript={transcript} episodeId="ep1" onSeekRequest={onSeek} locationKey={locationKey} />
            </PlayerProvider>
          </ToastProvider>
        )
        const { rerender } = render(wrap('entry-a'))
        expect(onSeek).toHaveBeenLastCalledWith(12)
        openAt('/podcasts/show/episodes/ep?t=3')
        rerender(wrap('entry-b'))
        expect(onSeek).toHaveBeenCalledTimes(2)
        expect(onSeek).toHaveBeenLastCalledWith(3)
      })

      it('does not carry one entry\'s offer onto another entry of the same episode', () => {
        setActivation(false)
        const onSeek = vi.fn()
        const transcript = defaultTranscript()
        const wrap = (locationKey: string) => (
          <ToastProvider>
            <PlayerProvider>
              <SegmentedTranscriptViewer transcript={transcript} episodeId="ep1" onSeekRequest={onSeek} locationKey={locationKey} />
            </PlayerProvider>
          </ToastProvider>
        )
        // Entry A: offer left pending.
        openAt('/podcasts/show/episodes/ep?t=12')
        const { rerender } = render(wrap('entry-a'))
        expect(screen.getByRole('button', { name: 'Play from 00:12' })).toBeInTheDocument()

        // Entry B: its own moment, taken up.
        openAt('/podcasts/show/episodes/ep?t=3')
        rerender(wrap('entry-b'))
        fireEvent.click(screen.getByRole('button', { name: 'Play from 00:03' }))
        expect(screen.queryByTestId('linked-moment')).toBeNull()

        // Back to A (its offer returns), Forward to B (settled: no offer).
        openAt('/podcasts/show/episodes/ep?t=12')
        rerender(wrap('entry-a'))
        expect(screen.getByRole('button', { name: 'Play from 00:12' })).toBeInTheDocument()
        openAt('/podcasts/show/episodes/ep?t=3')
        rerender(wrap('entry-b'))
        expect(screen.queryByTestId('linked-moment')).toBeNull()
      })

      it('moves focus to Play from and ties the note to it', () => {
        setActivation(false)
        openAt('/podcasts/show/episodes/ep#t=12')
        renderViewer({ transcript: defaultTranscript(), episodeId: 'ep1', onSeekRequest: vi.fn() })
        const play = screen.getByRole('button', { name: 'Play from 00:12' })
        expect(play).toHaveFocus()
        expect(play).toHaveAccessibleDescription('The moment this link points to')
      })

      it('takes focus on arrival only, not when a search moves the offer to another passage', async () => {
        setActivation(false)
        openAt('/podcasts/show/episodes/ep#t=5') // segment 1, "First segment"
        const user = userEvent.setup()
        renderViewer({ transcript: defaultTranscript(), episodeId: 'ep1', onSeekRequest: vi.fn() })
        expect(screen.getByRole('button', { name: 'Play from 00:05' })).toHaveFocus()

        // "shipping" hides segment 1 and matches segment 2: the offer moves there.
        const search = screen.getByRole('searchbox', { name: 'Search transcript' })
        await user.click(search)
        await user.type(search, 'shipping')
        await waitFor(() => expect(screen.getByTestId('segment-content-2')).toHaveAttribute('data-linked', 'true'))
        expect(search).toHaveFocus()
        expect(search).toHaveValue('shipping')
      })

      it('does not move the offer onto an unrelated match when a search hides the passage', async () => {
        setActivation(false)
        openAt('/podcasts/show/episodes/ep#t=12') // segment 2, "Second segment about shipping"
        const user = userEvent.setup()
        renderViewer({ transcript: defaultTranscript(), episodeId: 'ep1', onSeekRequest: vi.fn() })
        await user.type(screen.getByRole('searchbox', { name: 'Search transcript' }), 'First')
        await waitFor(() => expect(screen.queryByTestId('linked-moment')).toBeNull())
      })

      it('puts the button on the next visible passage when the linked one is hidden', () => {
        setActivation(false)
        openAt('/podcasts/show/episodes/ep#t=22') // inside the hidden filler segment
        const data = defaultTranscript()
        data.segments.push(seg({ id: 4, start: 25, end: 30, speaker: 'Bob', text: 'After the filler' }))
        renderViewer({ transcript: data, episodeId: 'ep1', onSeekRequest: vi.fn() })
        expect(screen.getByRole('button', { name: 'Play from 00:22' })).toBeInTheDocument()
        expect(screen.getByTestId('segment-content-4')).toHaveAttribute('data-linked', 'true')
      })

      it('puts the button on the first passage for a moment before the first segment', () => {
        setActivation(false)
        openAt('/podcasts/show/episodes/ep#t=0')
        const data = defaultTranscript()
        data.playback_time_offset_seconds = 5 // the first segment starts at 00:05
        const onSeek = vi.fn()
        renderViewer({ transcript: data, episodeId: 'ep1', onSeekRequest: onSeek })
        expect(screen.getByTestId('segment-content-1')).toHaveAttribute('data-linked', 'true')
        fireEvent.click(screen.getByRole('button', { name: 'Play from 00:00' }))
        expect(onSeek).toHaveBeenCalledWith(0)
      })

      it('scrolls to the visible passage that carries the offer when the linked one is hidden', () => {
        setActivation(false)
        openAt('/podcasts/show/episodes/ep#t=22') // inside the hidden filler segment (id 3)
        const scrolled: Element[] = []
        const original = Element.prototype.scrollIntoView
        Element.prototype.scrollIntoView = function (this: Element) {
          scrolled.push(this)
        }
        try {
          const data = defaultTranscript()
          data.segments.push(seg({ id: 4, start: 25, end: 30, speaker: 'Bob', text: 'After the filler' }))
          renderViewer({
            transcript: data,
            episodeId: 'ep1',
            onSeekRequest: vi.fn(),
            scrollToSegmentId: { segmentId: 3, nonce: 1 },
          })
          expect(scrolled).toEqual([screen.getByTestId('segment-content-4')])
        } finally {
          Element.prototype.scrollIntoView = original
        }
      })

      it('shows nothing without a moment in the URL', () => {
        setActivation(false)
        const onSeek = vi.fn()
        renderViewer({ transcript: defaultTranscript(), episodeId: 'ep1', onSeekRequest: onSeek })
        expect(onSeek).not.toHaveBeenCalled()
        expect(screen.queryByTestId('linked-moment')).toBeNull()
      })
    })

    it('fires onSeekRequest when Enter is pressed on a focused segment', () => {
      const onSeek = vi.fn()
      renderViewer({ transcript: defaultTranscript(), onSeekRequest: onSeek })
      const node = screen.getByRole('button', { name: /Seek to 00:00 — Alice/ })
      fireEvent.keyDown(node, { key: 'Enter' })
      expect(onSeek).toHaveBeenCalledWith(0)
    })

    it('persists the Follow playback toggle to localStorage', () => {
      renderViewer({ transcript: defaultTranscript() })
      const checkbox = screen.getByRole('checkbox', { name: /Follow playback/ })
      expect(checkbox).not.toBeChecked()
      fireEvent.click(checkbox)
      expect(checkbox).toBeChecked()
      expect(window.localStorage.getItem('thestill:transcript:followPlayback')).toBe('true')
    })

    it('scrolls to an explicit segment target', () => {
      const originalScrollIntoView = HTMLElement.prototype.scrollIntoView
      const scrollIntoView = vi.fn()
      HTMLElement.prototype.scrollIntoView = scrollIntoView

      try {
        renderViewer({
          transcript: defaultTranscript(),
          scrollToSegmentId: { segmentId: 2, nonce: 1 },
        })

        expect(scrollIntoView).toHaveBeenCalledTimes(1)
        expect(scrollIntoView.mock.contexts[0]).toBe(screen.getByTestId('segment-content-2'))
      } finally {
        HTMLElement.prototype.scrollIntoView = originalScrollIntoView
      }
    })

    it('collapses non-matching segments during search and keeps matches visible', () => {
      renderViewer({ transcript: defaultTranscript() })
      const input = screen.getByRole('searchbox', { name: /Search transcript/ })
      fireEvent.change(input, { target: { value: 'shipping' } })

      const mark = document.querySelector('mark')
      expect(mark?.textContent).toBe('shipping')
      expect(screen.queryByText('First segment')).toBeNull()
      expect(
        screen.getByRole('button', { name: /1 segment hidden/ }),
      ).toBeInTheDocument()
      expect(screen.getByText('1 / 1')).toBeInTheDocument()
    })

    it('expands a hidden group when the user clicks its placeholder', () => {
      renderViewer({ transcript: defaultTranscript() })
      const input = screen.getByRole('searchbox', { name: /Search transcript/ })
      fireEvent.change(input, { target: { value: 'shipping' } })
      fireEvent.click(screen.getByRole('button', { name: /1 segment hidden/ }))
      expect(screen.getByText('First segment')).toBeInTheDocument()
      expect(
        screen.queryByRole('button', { name: /segment hidden/ }),
      ).toBeNull()
    })

    it('surfaces No matches when the search term is not present', () => {
      renderViewer({ transcript: defaultTranscript() })
      const input = screen.getByRole('searchbox', { name: /Search transcript/ })
      fireEvent.change(input, { target: { value: 'absent needle' } })
      expect(screen.getByText('No matches')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Next match/ })).toBeDisabled()
    })
  })

  describe('kind toggles', () => {
    // Ads have no pill (spec #116 §6.1): an ad break is always one collapsed
    // row. Its own tests are in SegmentedTranscriptViewer.adBreaks.test.tsx.
    it('shows a pill for music, intro and outro when present, and none for ads', () => {
      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, kind: 'intro', text: 'welcome' }),
          seg({ id: 1, kind: 'music', text: 'theme plays' }),
          seg({ id: 2, text: 'main discussion' }),
          seg({ id: 3, kind: 'ad_break', text: 'ad copy', sponsor: 'Acme' }),
          seg({ id: 4, kind: 'outro', text: 'thanks for listening' }),
        ]),
      })

      expect(screen.queryByRole('button', { name: 'Ads' })).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Music' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Intro' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Outro' })).toBeInTheDocument()
    })

    it('only shows pills for kinds actually present', () => {
      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, text: 'pure content' }),
          seg({ id: 1, kind: 'ad_break', text: 'ad text' }),
        ]),
      })
      expect(screen.queryByRole('group', { name: 'Segment kind filters' })).not.toBeInTheDocument()
    })

    it('loads the hidden-kinds preference and collapses that kind to a chip', () => {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(['music']))
      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, text: 'content' }),
          seg({ id: 1, kind: 'music', text: 'theme plays' }),
        ]),
      })
      expect(screen.queryByText('theme plays')).not.toBeInTheDocument()
      expect(screen.getByTestId('segment-music-1')).toHaveAttribute('data-collapsed', 'true')
      expect(screen.getByRole('button', { name: 'Music' })).toHaveAttribute('aria-pressed', 'false')
    })

    it('persists the hidden-kinds preference to localStorage', async () => {
      const user = userEvent.setup()
      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, text: 'content' }),
          seg({ id: 1, kind: 'music', text: 'theme plays' }),
        ]),
      })
      await user.click(screen.getByRole('button', { name: 'Music' }))
      expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY)!)).toEqual(['music'])
    })

    it('ignores a stored ad_break entry and bogus values: an ad break is its one row either way', () => {
      for (const stored of [JSON.stringify(['ad_break']), '{"not": "an array"}']) {
        window.localStorage.setItem(STORAGE_KEY, stored)
        const view = renderViewer({
          transcript: makeTranscript([seg({ id: 0, kind: 'ad_break', text: 'ad copy', sponsor: 'Acme' })]),
        })
        expect(screen.getByTestId('ad-break-0')).toHaveAttribute('data-open', 'false')
        expect(screen.queryByText('ad copy')).not.toBeInTheDocument()
        view.unmount()
      }
    })

    it('renders music, intro and outro with their own block style', () => {
      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, kind: 'music', text: 'theme' }),
          seg({ id: 1, kind: 'intro', text: 'welcome folks' }),
          seg({ id: 2, kind: 'outro', text: 'goodbye' }),
        ]),
      })

      expect(screen.getByTestId('segment-music-0')).toBeInTheDocument()
      expect(screen.getByTestId('segment-intro-1')).toBeInTheDocument()
      expect(screen.getByTestId('segment-outro-2')).toBeInTheDocument()
    })

    it('handles an empty segment list with a helpful placeholder', () => {
      renderViewer({ transcript: makeTranscript([]) })
      expect(screen.getByText(/No segments available/i)).toBeInTheDocument()
    })
  })

  describe('speaker colours', () => {
    it('does not let an ad_break speaker consume a palette slot', () => {
      // An ad_break can carry a real speaker preserved from cleanup, but it
      // renders as a block, not a speaker row — so it must not shift the
      // content speakers' colours (which would desync vs the Legacy tab).
      renderViewer({
        transcript: makeTranscript([
          seg({ id: 1, kind: 'ad_break', speaker: 'AdVoice', text: 'sponsor copy' }),
          seg({ id: 2, kind: 'content', speaker: 'Alice', text: 'hello there' }),
        ]),
      })
      // Alice is the first *content* speaker → palette slot 0 (#0064A6).
      // Had the ad_break's speaker taken slot 0, Alice would be slot 1.
      // jsdom serialises the inline hex to rgb().
      const speakerLabel = screen
        .getByTestId('segment-content-2')
        .querySelector('span.font-semibold') as HTMLElement
      expect(speakerLabel.textContent).toContain('Alice')
      expect(speakerLabel.style.color).toBe('rgb(0, 100, 166)')
    })
  })

  describe('speaker labels', () => {
    const speaking: MentionLite = {
      id: 7,
      entity_id: 'person:alice',
      segment_id: 1,
      start_ms: 0,
      end_ms: 10_000,
      speaker: 'Alice',
      role: 'speaking',
      surface_form: 'Alice',
      quote_excerpt: 'First segment',
      confidence: 1,
      sentiment: null,
    }
    const alice: EpisodeEntity = {
      entity: { id: 'person:alice', type: 'person', canonical_name: 'Alice', wikidata_qid: null },
      mention_count: 1,
      first_mention_ms: 0,
      speaker_kind: 'host',
      salience: 1,
      mentions: [speaking],
    }

    function renderWithSpeaker(enabled = true, onSeekRequest = vi.fn()) {
      // The peek fetches the entity summary through react-query.
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } })
      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <ToastProvider>
              <PlayerProvider>
                <SegmentedTranscriptViewer
                  transcript={defaultTranscript()}
                  entitiesById={new Map([[alice.entity.id, alice]])}
                  mentionsBySegmentId={new Map([[1, [speaking]]])}
                  entityHighlightsEnabled={enabled}
                  onSeekRequest={onSeekRequest}
                />
              </PlayerProvider>
            </ToastProvider>
          </MemoryRouter>
        </QueryClientProvider>,
      )
      return onSeekRequest
    }

    it('links the label to the entity, with its own anchor, and opens the peek instead of seeking', () => {
      const onSeekRequest = renderWithSpeaker()
      const label = screen.getByRole('link', { name: /Alice, Person, 1 mention/ })
      expect(label).toHaveAttribute('href', '/entities/person/alice')
      expect(label).toHaveAttribute('id', 'm=person:alice:1:speaker')
      expect(label).toHaveAttribute('data-variant', 'speaker')
      expect(screen.getByTestId('segment-content-1')).toContainElement(label)
      fireEvent.click(label)
      expect(onSeekRequest).not.toHaveBeenCalled()
      expect(screen.getByTestId('entity-hover-card')).toHaveTextContent('host')
      // Bob's segment has no linked speaker: plain text.
      expect(screen.queryByRole('link', { name: /Bob/ })).not.toBeInTheDocument()
      expect(screen.getByTestId('segment-content-2')).toHaveTextContent('Bob:')
    })

    it('stays plain text while entity highlights are off', () => {
      renderWithSpeaker(false)
      expect(screen.queryByRole('link', { name: /Alice, Person/ })).not.toBeInTheDocument()
      expect(screen.getByTestId('segment-content-1')).toHaveTextContent('Alice:')
    })
  })
})
