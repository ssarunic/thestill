import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SpeakerPanel from './SpeakerPanel'
import { isUnnamed } from './segmentEditing'
import type { TranscriptSpeaker } from '../../api/types'

// Spec #110 §5.3 — the speakers column: name over raw label and count, play
// a sample, step through turns, focus (never filter), and the row menu.

const SPEAKERS: TranscriptSpeaker[] = [
  { label: 'SPEAKER_00', name: 'Ada Lovelace', entity_id: 'person:ada-lovelace', segment_ids: [1, 3] },
  { label: 'SPEAKER_01', name: 'SPEAKER_01', entity_id: null, segment_ids: [2] },
]

function setup(overrides: Partial<Parameters<typeof SpeakerPanel>[0]> = {}) {
  const props = {
    speakers: SPEAKERS,
    colors: new Map([
      ['SPEAKER_00', '#0064A6'],
      ['SPEAKER_01', '#C24400'],
    ]),
    focusLabel: null,
    onFocus: vi.fn(),
    onPlaySample: vi.fn(),
    onStep: vi.fn(),
    onRename: vi.fn(),
    onMerge: vi.fn(),
    onSaveAsHost: vi.fn(),
    ...overrides,
  }
  render(<SpeakerPanel {...props} />)
  return props
}

describe('SpeakerPanel', () => {
  it('lists every voice with its name, raw label, count and link state', () => {
    setup()
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText(/SPEAKER_00 · 2 segments/)).toBeInTheDocument()
    expect(screen.getByText('linked')).toBeInTheDocument()
    expect(screen.getByText('Unnamed')).toBeInTheDocument()
    expect(screen.getByText(/SPEAKER_01 · 1 segment$/)).toBeInTheDocument()
  })

  it('clicking a row focuses the voice and clicking again clears it', async () => {
    const props = setup()
    await userEvent.click(screen.getByTestId('speaker-row-SPEAKER_01'))
    expect(props.onFocus).toHaveBeenCalledWith('SPEAKER_01')
  })

  it('a focused row offers Clear focus and toggles off', async () => {
    const props = setup({ focusLabel: 'SPEAKER_01' })
    expect(screen.getByTestId('speaker-row-SPEAKER_01')).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(screen.getByRole('button', { name: 'Clear focus' }))
    expect(props.onFocus).toHaveBeenCalledWith(null)
    await userEvent.click(screen.getByTestId('speaker-row-SPEAKER_01'))
    expect(props.onFocus).toHaveBeenLastCalledWith(null)
  })

  it('play a sample and the turn steppers do not change the focus', async () => {
    const props = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Play a sample of SPEAKER_01' }))
    expect(props.onPlaySample).toHaveBeenCalledWith(SPEAKERS[1])
    await userEvent.click(screen.getByRole('button', { name: 'Next turn of Ada Lovelace' }))
    expect(props.onStep).toHaveBeenCalledWith(SPEAKERS[0], 1)
    await userEvent.click(screen.getByRole('button', { name: 'Previous turn of Ada Lovelace' }))
    expect(props.onStep).toHaveBeenCalledWith(SPEAKERS[0], -1)
    expect(props.onFocus).not.toHaveBeenCalled()
  })

  it('row menu: name, merge into another label, save as host', async () => {
    const props = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Actions for SPEAKER_01' }))
    expect(screen.queryByRole('button', { name: 'Save as podcast host' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Name or link…' }))
    expect(props.onRename).toHaveBeenCalledWith(SPEAKERS[1])
    expect(props.onFocus).not.toHaveBeenCalled()

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
