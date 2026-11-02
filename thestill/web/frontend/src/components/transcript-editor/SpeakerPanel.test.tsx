import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SpeakerPanel from './SpeakerPanel'
import { isUnnamed } from './segmentEditing'
import type { AnnotatedSegment, TranscriptSpeaker } from '../../api/types'

// Spec #110 §5 — the speaker panel: one row per diarization label, a row
// click filters, the row menu renames, merges and saves a host.

function seg(id: number, speaker: string): AnnotatedSegment {
  return {
    id,
    start: id * 10,
    end: id * 10 + 5,
    speaker,
    text: `segment ${id}`,
    kind: 'content',
    sponsor: null,
    source_segment_ids: [id],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
  }
}

const SPEAKERS: TranscriptSpeaker[] = [
  { label: 'SPEAKER_00', name: 'Ada Lovelace', entity_id: 'person:ada-lovelace', segment_ids: [1, 3] },
  { label: 'SPEAKER_01', name: 'SPEAKER_01', entity_id: null, segment_ids: [2] },
]

function setup(overrides: Partial<Parameters<typeof SpeakerPanel>[0]> = {}) {
  const props = {
    speakers: SPEAKERS,
    segments: [seg(1, 'Ada Lovelace'), seg(2, 'SPEAKER_01'), seg(3, 'Ada Lovelace')],
    selectedLabel: null,
    onSelect: vi.fn(),
    onRename: vi.fn(),
    onMerge: vi.fn(),
    onSaveAsHost: vi.fn(),
    ...overrides,
  }
  render(<SpeakerPanel {...props} />)
  return props
}

describe('SpeakerPanel', () => {
  it('lists every label with its name, count and link state', () => {
    setup()
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText(/SPEAKER_00 · 2 segments/)).toBeInTheDocument()
    expect(screen.getByText('linked')).toBeInTheDocument()
    expect(screen.getByText('Unnamed speaker')).toBeInTheDocument()
    expect(screen.getByText(/SPEAKER_01 · 1 segment$/)).toBeInTheDocument()
  })

  it('clicking a row filters to that label and clicking again clears', async () => {
    const props = setup()
    await userEvent.click(screen.getByTestId('speaker-row-SPEAKER_01'))
    expect(props.onSelect).toHaveBeenCalledWith('SPEAKER_01')
  })

  it('a selected row offers Show all segments', async () => {
    const props = setup({ selectedLabel: 'SPEAKER_01' })
    await userEvent.click(screen.getByRole('button', { name: 'Show all segments' }))
    expect(props.onSelect).toHaveBeenCalledWith(null)
    await userEvent.click(screen.getByTestId('speaker-row-SPEAKER_01'))
    expect(props.onSelect).toHaveBeenLastCalledWith(null)
  })

  it('row menu: name, merge into another label, save as host', async () => {
    const props = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Actions for SPEAKER_01' }))
    expect(screen.queryByRole('button', { name: 'Save as podcast host' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Name or link…' }))
    expect(props.onRename).toHaveBeenCalledWith(SPEAKERS[1])
    expect(props.onSelect).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Actions for SPEAKER_01' }))
    await userEvent.click(screen.getByRole('button', { name: 'Merge into…' }))
    await userEvent.click(screen.getByRole('button', { name: 'Ada Lovelace' }))
    expect(props.onMerge).toHaveBeenCalledWith(SPEAKERS[1], SPEAKERS[0])

    await userEvent.click(screen.getByRole('button', { name: 'Actions for Ada Lovelace' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save as podcast host' }))
    expect(props.onSaveAsHost).toHaveBeenCalledWith(SPEAKERS[0])
  })

  it('isUnnamed treats diarization labels and empty names as unnamed', () => {
    expect(isUnnamed({ label: 'SPEAKER_03', name: 'SPEAKER_03', entity_id: null, segment_ids: [] })).toBe(true)
    expect(isUnnamed({ label: 'SPEAKER_03', name: null, entity_id: null, segment_ids: [] })).toBe(true)
    expect(isUnnamed({ label: 'SPEAKER_03', name: 'Grace Hopper', entity_id: null, segment_ids: [] })).toBe(false)
  })
})
