import { describe, expect, it } from 'vitest'
import type { TranscriptEdit } from '../api/types'
import { undoRedoTargets } from './transcriptEditorTargets'

function edit(seq: number, over: Partial<TranscriptEdit> = {}): TranscriptEdit {
  return {
    id: `e${seq}`,
    seq,
    op: 'segment.set_text',
    anchor: {},
    args: {},
    before: {},
    reverts: null,
    session_id: 's1',
    report_id: null,
    actor_user_id: 'u',
    actor_kind: 'web',
    created_at: '2026-11-04T10:00:00Z',
    status: 'applied',
    conflict: null,
    ...over,
  }
}

const revert = (seq: number, of: number, status: TranscriptEdit['status'] = 'revert') =>
  edit(seq, { op: 'revert', reverts: `e${of}`, status })

describe('undoRedoTargets', () => {
  it('undoes the last live op of the session and nothing from other sessions', () => {
    const edits = [edit(1), edit(2, { session_id: 'other' }), edit(3, { status: 'reverted' }), revert(4, 3)]
    const targets = undoRedoTargets(edits, 's1')
    expect(targets.undo?.id).toBe('e1')
    expect(targets.redo?.id).toBe('e4')
  })

  it('offers redo while the undo is live and drops it once the undo is redone', () => {
    const undone = [edit(1, { status: 'reverted' }), revert(2, 1)]
    expect(undoRedoTargets(undone, 's1').redo?.id).toBe('e2')
    const redone = [edit(1), revert(2, 1, 'reverted'), revert(3, 2)]
    expect(undoRedoTargets(redone, 's1')).toEqual({ undo: expect.objectContaining({ id: 'e1' }), redo: null })
  })

  it('discards the redo path once a new edit follows the undo', () => {
    // Antigravity on #461: redo after a fresh edit revived the undone op and
    // conflicted the new one.
    const edits = [edit(1, { status: 'reverted' }), revert(2, 1), edit(3)]
    expect(undoRedoTargets(edits, 's1')).toEqual({ undo: expect.objectContaining({ id: 'e3' }), redo: null })
  })
})
