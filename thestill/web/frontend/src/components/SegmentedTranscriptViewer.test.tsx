import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnnotatedTranscriptDump } from '../api/types'
import { PlayerProvider } from '../contexts/PlayerContext'
import SegmentedTranscriptViewer from './SegmentedTranscriptViewer'
import { ToastProvider } from './Toast'

function transcript(): AnnotatedTranscriptDump {
  return {
    episode_id: 'ep-1',
    algorithm_version: 'test',
    playback_time_offset_seconds: 0,
    segments: [
      {
        id: 1,
        start: 0,
        end: 10,
        speaker: 'Alice',
        text: 'First segment',
        kind: 'content',
        sponsor: null,
        source_segment_ids: [],
        source_word_span: null,
        user_segment_id: null,
        metadata: {},
      },
      {
        id: 2,
        start: 10,
        end: 20,
        speaker: 'Bob',
        text: 'Second segment about shipping',
        kind: 'content',
        sponsor: null,
        source_segment_ids: [],
        source_word_span: null,
        user_segment_id: null,
        metadata: {},
      },
      {
        id: 3,
        start: 20,
        end: 25,
        speaker: 'Alice',
        text: 'um filler words',
        kind: 'filler',
        sponsor: null,
        source_segment_ids: [],
        source_word_span: null,
        user_segment_id: null,
        metadata: {},
      },
    ],
  }
}

function renderWithPlayer(ui: React.ReactElement) {
  return render(
    <ToastProvider>
      <PlayerProvider>{ui}</PlayerProvider>
    </ToastProvider>,
  )
}

describe('SegmentedTranscriptViewer', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('renders every non-filler segment by default', () => {
    renderWithPlayer(<SegmentedTranscriptViewer transcript={transcript()} />)
    expect(screen.getByText('First segment')).toBeInTheDocument()
    expect(screen.getByText(/Second segment about shipping/)).toBeInTheDocument()
    expect(screen.queryByText(/um filler words/)).toBeNull()
  })

  it('reveals filler segments when Show filler is toggled on', () => {
    renderWithPlayer(<SegmentedTranscriptViewer transcript={transcript()} />)
    fireEvent.click(screen.getByRole('checkbox', { name: /Show filler/ }))
    expect(screen.getByText(/um filler words/)).toBeInTheDocument()
    expect(window.localStorage.getItem('thestill:transcript:showFiller')).toBe('true')
  })

  it('does not expose seek affordance when no handler is provided', () => {
    renderWithPlayer(<SegmentedTranscriptViewer transcript={transcript()} />)
    expect(screen.queryByRole('button', { name: /Seek to/ })).toBeNull()
  })

  it('fires onSeekRequest with absolute seconds when a segment is clicked', () => {
    const onSeek = vi.fn()
    const data = transcript()
    data.playback_time_offset_seconds = 100
    renderWithPlayer(<SegmentedTranscriptViewer transcript={data} onSeekRequest={onSeek} />)
    fireEvent.click(screen.getByRole('button', { name: /Seek to 01:50 — Bob/ }))
    expect(onSeek).toHaveBeenCalledWith(110)
  })

  it('fires onSeekRequest when Enter is pressed on a focused segment', () => {
    const onSeek = vi.fn()
    renderWithPlayer(<SegmentedTranscriptViewer transcript={transcript()} onSeekRequest={onSeek} />)
    const node = screen.getByRole('button', { name: /Seek to 00:00 — Alice/ })
    fireEvent.keyDown(node, { key: 'Enter' })
    expect(onSeek).toHaveBeenCalledWith(0)
  })

  it('persists the Follow playback toggle to localStorage', () => {
    renderWithPlayer(<SegmentedTranscriptViewer transcript={transcript()} />)
    const checkbox = screen.getByRole('checkbox', { name: /Follow playback/ })
    expect(checkbox).not.toBeChecked()
    fireEvent.click(checkbox)
    expect(checkbox).toBeChecked()
    expect(window.localStorage.getItem('thestill:transcript:followPlayback')).toBe('true')
  })

  it('filters transcript rendering to segments containing the search term', () => {
    renderWithPlayer(<SegmentedTranscriptViewer transcript={transcript()} />)
    const input = screen.getByRole('searchbox', { name: /Search transcript/ })
    fireEvent.change(input, { target: { value: 'shipping' } })
    const matchCount = screen.getByText('1')
    expect(matchCount).toBeInTheDocument()
    // The non-matching segment's container should carry the dimmed class.
    const firstSegment = screen.getByText('First segment').closest('[data-active]') as HTMLElement
    expect(firstSegment.className).toMatch(/opacity-40/)
  })
})
