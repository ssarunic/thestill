import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  AnnotatedSegment,
  AnnotatedTranscriptDump,
  SegmentKind,
} from '../api/types'
import { PlayerProvider } from '../contexts/PlayerContext'
import SegmentedTranscriptViewer from './SegmentedTranscriptViewer'
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
    it('shows ad body text without a summary label when Ads is on', () => {
      renderViewer({
        transcript: makeTranscript([
          seg({
            id: 0,
            kind: 'ad_break',
            sponsor: 'Acme',
            text: 'Support for the show comes from Acme. Visit acme.com.',
          }),
        ]),
      })

      // Full ad copy reads like body text.
      expect(screen.getByText(/acme\.com/)).toBeInTheDocument()
      // No "AD BREAK — …" chip above the body when expanded.
      expect(screen.queryByText(/Ad break/i)).not.toBeInTheDocument()
    })

    it('collapses ads to a compact "AD BREAK — Sponsor" chip when Ads is off', async () => {
      const user = userEvent.setup()
      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, text: 'before ad', speaker: 'Alice' }),
          seg({ id: 1, kind: 'ad_break', sponsor: 'Acme', text: 'visit acme.com' }),
          seg({ id: 2, text: 'after ad', speaker: 'Alice' }),
        ]),
      })

      // Ads ON: body visible, no chip label.
      expect(screen.getByText(/visit acme\.com/)).toBeInTheDocument()
      expect(screen.queryByText(/Ad break/i)).not.toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: 'Ads' }))

      // Ads OFF: body gone, compact chip "Ad break — Acme" visible
      // (uppercase is CSS-only, so the DOM text is "Ad break").
      expect(screen.queryByText(/visit acme\.com/)).not.toBeInTheDocument()
      expect(screen.getByText(/Ad break/)).toBeInTheDocument()
      expect(screen.getByText(/Acme/)).toBeInTheDocument()
      // Surrounding content is unaffected.
      expect(screen.getByText('before ad')).toBeInTheDocument()
      expect(screen.getByText('after ad')).toBeInTheDocument()
    })

    it('only shows toggles for kinds actually present', () => {
      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, text: 'pure content' }),
          seg({ id: 1, kind: 'ad_break', text: 'ad text' }),
        ]),
      })

      expect(screen.getByRole('button', { name: 'Ads' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Music' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Intro' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Outro' })).not.toBeInTheDocument()
    })

    it('renders toggles for every present non-content kind', () => {
      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, kind: 'intro', text: 'welcome' }),
          seg({ id: 1, kind: 'music', text: 'theme plays' }),
          seg({ id: 2, text: 'main discussion' }),
          seg({ id: 3, kind: 'ad_break', text: 'ad copy', sponsor: 'Acme' }),
          seg({ id: 4, kind: 'outro', text: 'thanks for listening' }),
        ]),
      })

      expect(screen.getByRole('button', { name: 'Ads' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Music' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Intro' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Outro' })).toBeInTheDocument()
    })

    it('loads the hidden-kinds preference from localStorage and starts collapsed', () => {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(['ad_break']))

      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, text: 'content' }),
          seg({ id: 1, kind: 'ad_break', text: 'hidden ad', sponsor: 'Acme' }),
        ]),
      })

      expect(screen.getByText('content')).toBeInTheDocument()
      // Ad body is collapsed to the chip, so the body text is absent.
      expect(screen.queryByText('hidden ad')).not.toBeInTheDocument()
      // But the compact chip is still there so the reader sees the ad position.
      expect(screen.getByText(/Ad break/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Ads' })).toHaveAttribute('aria-pressed', 'false')
    })

    it('persists the hidden-kinds preference to localStorage', async () => {
      const user = userEvent.setup()
      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, text: 'content' }),
          seg({ id: 1, kind: 'ad_break', text: 'ad copy' }),
        ]),
      })

      await user.click(screen.getByRole('button', { name: 'Ads' }))

      const stored = window.localStorage.getItem(STORAGE_KEY)
      expect(stored).not.toBeNull()
      expect(JSON.parse(stored!)).toEqual(['ad_break'])
    })

    it('falls back to defaults when localStorage has bogus values', () => {
      window.localStorage.setItem(STORAGE_KEY, '{"not": "an array"}')

      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, kind: 'ad_break', text: 'ad visible' }),
        ]),
      })

      expect(screen.getByText('ad visible')).toBeInTheDocument()
    })

    it('keeps the chip visible even when every togglable kind is collapsed', async () => {
      const user = userEvent.setup()
      renderViewer({
        transcript: makeTranscript([
          seg({ id: 0, kind: 'ad_break', text: 'ad copy', sponsor: 'Acme' }),
        ]),
      })

      await user.click(screen.getByRole('button', { name: 'Ads' }))

      // Body hidden, but the reader still sees the ad marker.
      expect(screen.queryByText('ad copy')).not.toBeInTheDocument()
      expect(screen.getByText(/Ad break/)).toBeInTheDocument()
      expect(screen.getByText(/Acme/)).toBeInTheDocument()
      const block = screen.getByTestId('segment-ad_break-0')
      expect(block).toHaveAttribute('data-collapsed', 'true')
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
})
