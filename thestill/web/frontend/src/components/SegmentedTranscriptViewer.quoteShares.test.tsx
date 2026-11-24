import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AnnotatedSegment, AnnotatedTranscriptDump, QuoteShareItem, SegmentKind } from '../api/types'
import { PlayerProvider } from '../contexts/PlayerContext'
import SegmentedTranscriptViewer from './SegmentedTranscriptViewer'
import { ToastProvider } from './Toast'

const track = vi.hoisted(() => vi.fn())
vi.mock('../analytics/client', () => ({ track }))

function seg(id: number, speaker: string, text: string, kind: SegmentKind = 'content'): AnnotatedSegment {
  return {
    id,
    start: id * 10,
    end: id * 10 + 10,
    speaker,
    text,
    kind,
    sponsor: null,
    source_segment_ids: [id],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
  }
}

const transcript: AnnotatedTranscriptDump = {
  episode_id: 'ep1',
  segments: [
    seg(1, 'Alice', 'Opening chat. The best teams change their minds fast when the evidence changes. Then more talk.'),
    seg(2, 'Bob', 'Something else entirely, with no public quote in it.'),
    seg(3, 'Alice', 'This turn was edited since the quote was cut.'),
  ],
  playback_time_offset_seconds: 0,
  algorithm_version: 'v1',
  transcript_source_duration_s: null,
  cleaning: null,
}

const shares: QuoteShareItem[] = [
  {
    id: 'k7m2xq3dpa',
    segment_id: 1,
    text: 'The best teams change their minds  fast when the evidence changes.',
    start_seconds: 10,
    speaker: 'Alice',
  },
  // Its turn no longer holds these words: no icon rather than a wrong one.
  { id: 'aaaaaaaaaa', segment_id: 3, text: 'Words that are not in that turn any more.', start_seconds: 30, speaker: 'Alice' },
]

function renderViewer(quoteShares?: QuoteShareItem[]) {
  return render(
    <ToastProvider>
      <PlayerProvider>
        <SegmentedTranscriptViewer transcript={transcript} quoteShares={quoteShares} />
      </PlayerProvider>
    </ToastProvider>,
  )
}

describe('sharing a quote from the transcript (spec #114, Rule A)', () => {
  const writeText = vi.fn(async () => {})
  afterEach(() => {
    writeText.mockClear()
    track.mockClear()
  })

  it('puts a share icon only on a turn that holds a public quote', () => {
    renderViewer(shares)
    expect(screen.getByTestId('quote-share-1')).toBeInTheDocument()
    expect(screen.queryByTestId('quote-share-2')).toBeNull()
    expect(screen.queryByTestId('quote-share-3')).toBeNull()
  })

  it('offers nothing without shares (single-user installs, private shows)', () => {
    renderViewer([])
    expect(screen.queryByRole('button', { name: /Share a quote/ })).toBeNull()
    renderViewer(undefined)
    expect(screen.queryByRole('button', { name: /Share a quote/ })).toBeNull()
  })

  it('opens the card and copies the short link', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    renderViewer(shares)
    fireEvent.click(screen.getByRole('button', { name: 'Share a quote from Alice' }))

    const dialog = screen.getByRole('dialog', { name: 'Share this quote' })
    expect(dialog.querySelector('img')).toHaveAttribute('src', '/q/k7m2xq3dpa.png')
    fireEvent.click(screen.getByRole('button', { name: 'Copy link' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/q/k7m2xq3dpa`))
    expect(track).toHaveBeenCalledWith('share', { kind: 'quote' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(await screen.findByText('Quote link copied')).toBeInTheDocument()
  })

  it('closes on Escape without sharing', () => {
    renderViewer(shares)
    fireEvent.click(screen.getByRole('button', { name: 'Share a quote from Alice' }))
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(track).not.toHaveBeenCalled()
  })
})
