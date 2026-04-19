import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnnotatedTranscriptDump } from '../api/types'
import { PlayerProvider } from '../contexts/PlayerContext'
import SegmentedTranscriptViewer from './SegmentedTranscriptViewer'

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
        text: 'Second segment',
        kind: 'content',
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
  return render(<PlayerProvider>{ui}</PlayerProvider>)
}

describe('SegmentedTranscriptViewer', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('renders every non-filler segment', () => {
    renderWithPlayer(<SegmentedTranscriptViewer transcript={transcript()} />)
    expect(screen.getByText('First segment')).toBeInTheDocument()
    expect(screen.getByText('Second segment')).toBeInTheDocument()
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
})
