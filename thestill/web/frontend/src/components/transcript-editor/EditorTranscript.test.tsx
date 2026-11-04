import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import EditorTranscript from './EditorTranscript'
import type { SegmentEditing } from './segmentEditing'
import type { AnnotatedSegment, KaraokeWordsByEpisode } from '../../api/types'

// Spec #110 §5.4 — rows with a gutter, click-a-word playback on the
// selected row, the merge handle between adjacent rows of one kind, and
// voice focus that dims instead of hiding.

function seg(id: number, speaker: string, text: string, kind: AnnotatedSegment['kind'] = 'content'): AnnotatedSegment {
  return {
    id,
    start: id * 10,
    end: id * 10 + 5,
    speaker,
    text,
    kind,
    sponsor: null,
    source_segment_ids: [id],
    source_word_span: null,
    user_segment_id: null,
    metadata: id === 2 ? { edited: true } : {},
  }
}

const SEGMENTS = [seg(1, 'Ada', 'Welcome back'), seg(2, 'Grace', 'Thanks Ada'), seg(3, 'Ada', 'Sponsor read', 'ad_break')]

function editing(): SegmentEditing {
  return {
    labelsBySegmentId: new Map([
      [1, 'SPEAKER_00'],
      [2, 'SPEAKER_01'],
      [3, 'SPEAKER_00'],
    ]),
    speakers: [
      { label: 'SPEAKER_00', name: 'Ada', entity_id: null, segment_ids: [1, 3] },
      { label: 'SPEAKER_01', name: 'Grace', entity_id: null, segment_ids: [2] },
    ],
    editingSegmentId: null,
    onEditSegment: vi.fn(),
    onSetSpeaker: vi.fn(),
    onPickSpeakerName: vi.fn(),
    onSetText: vi.fn(),
    onSetKind: vi.fn(),
    onSplit: vi.fn(),
    onMergeWithPrevious: vi.fn(),
    disabled: false,
  }
}

function setup(overrides: Partial<Parameters<typeof EditorTranscript>[0]> = {}) {
  const props = {
    segments: SEGMENTS,
    offset: 0,
    colors: new Map([
      ['SPEAKER_00', '#0064A6'],
      ['SPEAKER_01', '#C24400'],
    ]),
    labelsBySegmentId: editing().labelsBySegmentId,
    editing: editing(),
    selectedId: null,
    activeId: null,
    focusLabel: null,
    words: null,
    conflictSegmentIds: new Set<number>(),
    onSelect: vi.fn(),
    onSeek: vi.fn(),
    onMerge: vi.fn(),
    registerRef: () => () => {},
    ...overrides,
  }
  render(<EditorTranscript {...props} />)
  return props
}

describe('EditorTranscript', () => {
  it('renders the gutter with name, times and tags', () => {
    setup()
    expect(screen.getByTestId('editor-row-1')).toHaveTextContent('Ada')
    expect(screen.getByTestId('editor-row-1')).toHaveTextContent('0:10.0')
    expect(screen.getByTestId('editor-row-2')).toHaveTextContent('edited')
    expect(screen.getByTestId('editor-row-3')).toHaveTextContent('ad')
  })

  it('clicking a row selects it and the start time plays from it', async () => {
    const props = setup()
    await userEvent.click(screen.getByText('Thanks Ada'))
    expect(props.onSelect).toHaveBeenCalledWith(2)
    await userEvent.click(screen.getAllByTitle('Play from here')[1])
    expect(props.onSeek).toHaveBeenCalledWith(20)
  })

  it('the selected row shows its words and a word click seeks to it', async () => {
    const words: KaraokeWordsByEpisode = {
      episodeId: 'ep',
      offset: 0,
      wordsBySegmentId: new Map([
        [
          2,
          [
            { w: 'Thanks', s: 20.0, e: 20.4 },
            { w: 'Ada', s: 20.5, e: 20.9 },
          ],
        ],
      ]),
    }
    const props = setup({ words, selectedId: 2 })
    await userEvent.click(screen.getByTitle('Play from 0:20.5'))
    expect(props.onSeek).toHaveBeenCalledWith(20.5)
    expect(props.onSelect).not.toHaveBeenCalled()
  })

  it('a merge handle appears only between adjacent rows of one kind', async () => {
    const props = setup({ selectedId: 2 })
    const handles = screen.getAllByRole('button', { name: 'Merge segments' })
    expect(handles).toHaveLength(1) // rows 1→2 (content); 2→3 is content→ad
    await userEvent.click(handles[0])
    expect(props.onMerge).toHaveBeenCalledWith(SEGMENTS[0], SEGMENTS[1])
  })

  it('focusing a voice dims the others but keeps them', () => {
    setup({ focusLabel: 'SPEAKER_01' })
    expect(screen.getByTestId('editor-row-1').className).toContain('opacity-40')
    expect(screen.getByTestId('editor-row-2').className).not.toContain('opacity-40')
    expect(screen.getByText('Welcome back')).toBeInTheDocument()
  })

  it('double-click opens the text for editing', async () => {
    const props = setup()
    await userEvent.dblClick(screen.getByText('Welcome back'))
    expect(props.editing.onEditSegment).toHaveBeenCalledWith(1)
  })
})
