import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { EditableText, KindPicker, SpeakerPicker } from './EditableSegment'
import { wordIndexAtCaret, type SegmentEditing } from './segmentEditing'
import type { AnnotatedSegment } from '../../api/types'

// Spec #110 §5 "Segments" — click the speaker to reassign, edit text inline
// (Enter splits at the caret on a word boundary, Backspace at the start
// merges with the previous segment), the kind picker.

function seg(overrides: Partial<AnnotatedSegment> = {}): AnnotatedSegment {
  return {
    id: 7,
    start: 10,
    end: 15,
    speaker: 'Ada Lovelace',
    text: 'Welcome back to the show everyone',
    kind: 'content',
    sponsor: null,
    source_segment_ids: [7],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
    ...overrides,
  }
}

function editing(overrides: Partial<SegmentEditing> = {}): SegmentEditing {
  return {
    labelsBySegmentId: new Map([[7, 'SPEAKER_00']]),
    speakers: [
      { label: 'SPEAKER_00', name: 'Ada Lovelace', entity_id: null, segment_ids: [7] },
      { label: 'SPEAKER_01', name: 'Grace Hopper', entity_id: null, segment_ids: [8] },
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
    ...overrides,
  }
}

describe('wordIndexAtCaret', () => {
  const text = 'Welcome back to the show'
  it('lands before the word after a space', () => {
    expect(wordIndexAtCaret(text, 'Welcome back '.length)).toBe(2)
  })
  it('a caret mid-word splits before that word', () => {
    expect(wordIndexAtCaret(text, 'Welcome ba'.length)).toBe(1)
  })
  it('either end is no split', () => {
    expect(wordIndexAtCaret(text, 0)).toBeNull()
    expect(wordIndexAtCaret(text, text.length)).toBeNull()
  })
})

describe('SpeakerPicker', () => {
  it('lists the labels, marks the current one and reassigns on pick', async () => {
    const e = editing()
    render(<SpeakerPicker segment={seg()} editing={e} color="#0064A6" />)
    await userEvent.click(screen.getByRole('button', { name: 'Ada Lovelace' }))
    expect(screen.getByRole('button', { name: /Ada Lovelace.*SPEAKER_00/ })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(screen.getByRole('button', { name: /Grace Hopper.*SPEAKER_01/ }))
    expect(e.onSetSpeaker).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }), { label: 'SPEAKER_01' })
  })

  it('"Someone else…" opens the name dialog', async () => {
    const e = editing()
    render(<SpeakerPicker segment={seg()} editing={e} color="#0064A6" />)
    await userEvent.click(screen.getByRole('button', { name: 'Ada Lovelace' }))
    await userEvent.click(screen.getByRole('button', { name: 'Someone else…' }))
    expect(e.onPickSpeakerName).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }))
  })
})

describe('KindPicker', () => {
  it('changes the kind', async () => {
    const e = editing()
    render(<KindPicker segment={seg()} editing={e} />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Segment kind' }), 'ad_break')
    expect(e.onSetKind).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }), 'ad_break')
  })
})

describe('EditableText', () => {
  it('opens on click', async () => {
    const e = editing()
    render(
      <EditableText segment={seg()} editing={e}>
        Welcome back to the show everyone
      </EditableText>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Edit text' }))
    expect(e.onEditSegment).toHaveBeenCalledWith(7)
  })

  it('Enter with changed text saves it', async () => {
    const e = editing({ editingSegmentId: 7 })
    render(
      <EditableText segment={seg()} editing={e}>
        x
      </EditableText>,
    )
    const field = screen.getByRole('textbox', { name: 'Segment text' })
    await userEvent.clear(field)
    await userEvent.type(field, 'Welcome back, everyone')
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(e.onSetText).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }), 'Welcome back, everyone')
    expect(e.onSplit).not.toHaveBeenCalled()
    expect(e.onEditSegment).toHaveBeenLastCalledWith(null)
  })

  it('Enter inside unchanged text splits at the caret word', () => {
    const e = editing({ editingSegmentId: 7 })
    render(
      <EditableText segment={seg()} editing={e}>
        x
      </EditableText>,
    )
    const field = screen.getByRole('textbox', { name: 'Segment text' }) as HTMLTextAreaElement
    field.setSelectionRange('Welcome back '.length, 'Welcome back '.length)
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(e.onSplit).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }), 2)
    expect(e.onSetText).not.toHaveBeenCalled()
  })

  it('Backspace at the start merges with the previous segment', () => {
    const e = editing({ editingSegmentId: 7 })
    render(
      <EditableText segment={seg()} editing={e}>
        x
      </EditableText>,
    )
    const field = screen.getByRole('textbox', { name: 'Segment text' }) as HTMLTextAreaElement
    field.setSelectionRange(0, 0)
    fireEvent.keyDown(field, { key: 'Backspace' })
    expect(e.onMergeWithPrevious).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }))
  })

  it('Escape cancels without saving', async () => {
    const e = editing({ editingSegmentId: 7 })
    render(
      <EditableText segment={seg()} editing={e}>
        x
      </EditableText>,
    )
    const field = screen.getByRole('textbox', { name: 'Segment text' })
    await userEvent.type(field, ' more')
    fireEvent.keyDown(field, { key: 'Escape' })
    expect(e.onSetText).not.toHaveBeenCalled()
    expect(e.onEditSegment).toHaveBeenLastCalledWith(null)
  })

  it('a disabled editor does not open', async () => {
    const e = editing({ disabled: true })
    render(
      <EditableText segment={seg()} editing={e}>
        x
      </EditableText>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Edit text' }))
    expect(e.onEditSegment).not.toHaveBeenCalled()
  })
})
