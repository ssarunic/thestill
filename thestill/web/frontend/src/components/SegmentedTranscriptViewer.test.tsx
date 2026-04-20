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

  it('collapses non-matching segments during search and keeps matches visible', () => {
    renderWithPlayer(<SegmentedTranscriptViewer transcript={transcript()} />)
    const input = screen.getByRole('searchbox', { name: /Search transcript/ })
    fireEvent.change(input, { target: { value: 'shipping' } })

    // The matched term is wrapped in a <mark>, so query by the mark element.
    const mark = document.querySelector('mark')
    expect(mark?.textContent).toBe('shipping')
    expect(screen.queryByText('First segment')).toBeNull()
    expect(
      screen.getByRole('button', { name: /1 segment hidden/ }),
    ).toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
  })

  it('expands a hidden group when the user clicks its placeholder', () => {
    renderWithPlayer(<SegmentedTranscriptViewer transcript={transcript()} />)
    const input = screen.getByRole('searchbox', { name: /Search transcript/ })
    fireEvent.change(input, { target: { value: 'shipping' } })
    fireEvent.click(screen.getByRole('button', { name: /1 segment hidden/ }))
    expect(screen.getByText('First segment')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /segment hidden/ }),
    ).toBeNull()
  })

  it('surfaces No matches when the search term is not present', () => {
    renderWithPlayer(<SegmentedTranscriptViewer transcript={transcript()} />)
    const input = screen.getByRole('searchbox', { name: /Search transcript/ })
    fireEvent.change(input, { target: { value: 'absent needle' } })
    expect(screen.getByText('No matches')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Next match/ })).toBeDisabled()
  })
})
