import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SegmentInspector from './SegmentInspector'
import type { AnnotatedSegment, TranscriptSpeaker } from '../../api/types'

// Spec #110 §5.3 — the selected row's properties: speaker, kind, times,
// Edit text and Hide.

const SPEAKERS: TranscriptSpeaker[] = [
  { label: 'SPEAKER_00', name: 'Ada Lovelace', entity_id: null, segment_ids: [1] },
  { label: 'SPEAKER_01', name: 'Grace Hopper', entity_id: null, segment_ids: [2] },
]

const SEGMENT: AnnotatedSegment = {
  id: 2,
  start: 30.2,
  end: 36.4,
  speaker: 'Grace Hopper',
  text: 'Thanks for having me.',
  kind: 'content',
  sponsor: null,
  source_segment_ids: [2],
  source_word_span: null,
  user_segment_id: null,
  metadata: {},
}

function setup(overrides: Partial<Parameters<typeof SegmentInspector>[0]> = {}) {
  const props = {
    segment: SEGMENT,
    label: 'SPEAKER_01',
    speakers: SPEAKERS,
    offset: 0,
    disabled: false,
    onSetSpeaker: vi.fn(),
    onPickSpeakerName: vi.fn(),
    onSetKind: vi.fn(),
    onEditText: vi.fn(),
    ...overrides,
  }
  render(<SegmentInspector {...props} />)
  return props
}

describe('SegmentInspector', () => {
  it('asks for a selection when nothing is selected', () => {
    setup({ segment: null, label: null })
    expect(screen.getByText(/Select a row/)).toBeInTheDocument()
  })

  it('shows the times and changes speaker and kind', async () => {
    const props = setup()
    expect(screen.getByText(/0:30\.2 – 0:36\.4/)).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Speaker' }), 'SPEAKER_00')
    expect(props.onSetSpeaker).toHaveBeenCalledWith(SEGMENT, { label: 'SPEAKER_00' })
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Speaker' }), '__someone_else__')
    expect(props.onPickSpeakerName).toHaveBeenCalledWith(SEGMENT)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Kind' }), 'ad_break')
    expect(props.onSetKind).toHaveBeenCalledWith(SEGMENT, 'ad_break')
  })

  it('Edit text and Hide call back; Hide is gone on a filler row', async () => {
    const props = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Edit text' }))
    expect(props.onEditText).toHaveBeenCalledWith(SEGMENT)
    await userEvent.click(screen.getByRole('button', { name: 'Hide' }))
    expect(props.onSetKind).toHaveBeenCalledWith(SEGMENT, 'filler')
  })

  it('a filler row has no Hide button', () => {
    setup({ segment: { ...SEGMENT, kind: 'filler' } })
    expect(screen.queryByRole('button', { name: 'Hide' })).not.toBeInTheDocument()
  })
})
