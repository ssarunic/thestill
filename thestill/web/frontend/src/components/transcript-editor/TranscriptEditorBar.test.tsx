import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import TranscriptEditorBar from './TranscriptEditorBar'
import type { TranscriptEdit, TranscriptEditConflict } from '../../api/types'

// Spec #110 §5.2 — Back, save status, undo / redo (⌘Z, ⇧⌘Z), the
// conflicts count with the list behind it, Finish editing.

function edit(seq: number, status: TranscriptEdit['status'], op: TranscriptEdit['op'] = 'speaker.rename_label'): TranscriptEdit {
  return {
    id: `e-${seq}`,
    seq,
    op,
    anchor: { label: 'SPEAKER_01' },
    args: { name: 'Grace Hopper', entity_id: null },
    before: {},
    reverts: null,
    session_id: 's',
    report_id: null,
    actor_user_id: null,
    actor_kind: 'web',
    created_at: '2026-11-02T12:00:00+00:00',
    status,
    conflict: null,
  }
}

function setup(overrides: Partial<Parameters<typeof TranscriptEditorBar>[0]> = {}) {
  const props = {
    backTo: '/podcasts/show/episodes/ep?view=transcript',
    title: 'OKRs',
    subtitle: 'Product Thinking',
    saveStatus: 'saved' as const,
    saveError: null,
    canUndo: true,
    canRedo: false,
    busy: false,
    conflicts: [] as TranscriptEditConflict[],
    edits: [edit(1, 'applied'), edit(2, 'applied'), edit(3, 'reverted')],
    locked: false,
    finishing: false,
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    onDropConflict: vi.fn(),
    onFinish: vi.fn(),
    ...overrides,
  }
  render(
    <MemoryRouter>
      <TranscriptEditorBar {...props} />
    </MemoryRouter>,
  )
  return props
}

describe('TranscriptEditorBar', () => {
  it('shows the episode, the save status and the live edit count', () => {
    setup()
    expect(screen.getByText('OKRs')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the episode' })).toHaveAttribute('href', '/podcasts/show/episodes/ep?view=transcript')
    expect(screen.getByRole('status')).toHaveTextContent('Saved')
    expect(screen.getByText('2 edits')).toBeInTheDocument()
  })

  it('undo and redo follow what the session can do, by button and by keyboard', async () => {
    const props = setup({ canUndo: true, canRedo: true })
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(props.onUndo).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('button', { name: 'Redo' }))
    expect(props.onRedo).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(window, { key: 'z', metaKey: true })
    expect(props.onUndo).toHaveBeenCalledTimes(2)
    fireEvent.keyDown(window, { key: 'z', metaKey: true, shiftKey: true })
    expect(props.onRedo).toHaveBeenCalledTimes(2)
  })

  it('a disabled undo ignores the shortcut and the button', () => {
    const props = setup({ canUndo: false })
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled()
    fireEvent.keyDown(window, { key: 'z', metaKey: true })
    expect(props.onUndo).not.toHaveBeenCalled()
  })

  it('the conflicts chip opens the list and Drop reverts the op', async () => {
    const conflict: TranscriptEditConflict = {
      edit_id: 'e-2',
      seq: 2,
      op: 'segment.set_text',
      reason: 'text_changed',
      detail: { current_text: 'what the re-clean wrote' },
    }
    const edits = [edit(1, 'applied'), { ...edit(2, 'conflict', 'segment.set_text'), args: { old_text: 'a', new_text: 'what I wrote' } }]
    const props = setup({ conflicts: [conflict], edits })
    await userEvent.click(screen.getByRole('button', { name: '1 conflict' }))
    expect(screen.getByText('A re-clean rewrote this segment.')).toBeInTheDocument()
    expect(screen.getByText('what I wrote')).toBeInTheDocument()
    expect(screen.getByText('what the re-clean wrote')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Drop' }))
    expect(props.onDropConflict).toHaveBeenCalledWith('e-2')
  })

  it('a locked episode explains itself and disables Finish', () => {
    setup({ locked: true })
    expect(screen.getByText(/A transcribe or clean is running/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Finish editing' })).toBeDisabled()
  })

  it('Finish calls back and errors are announced', async () => {
    const props = setup({ saveStatus: 'stale', saveError: 'The transcript changed elsewhere.' })
    await userEvent.click(screen.getByRole('button', { name: 'Finish editing' }))
    expect(props.onFinish).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('alert')).toHaveTextContent('The transcript changed elsewhere.')
  })
})
