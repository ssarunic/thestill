import type { TranscriptEdit } from '../api/types'

// Spec #110 §2 — undo and redo over this session's rows of the op log.
// Undo reverts the session's last live op; redo reverts the session's last
// live undo. A new edit after an undo discards the redo path, as in any
// editor: redoing then would revive an op the new edit was anchored
// without, and turn the new edit into a conflict.

export interface UndoRedoTargets {
  undo: TranscriptEdit | null
  redo: TranscriptEdit | null
}

export function undoRedoTargets(edits: TranscriptEdit[], sessionId: string): UndoRedoTargets {
  const session = edits.filter((e) => e.session_id === sessionId)
  const undo = [...session].reverse().find((e) => e.op !== 'revert' && e.status !== 'reverted') ?? null
  const byId = new Map(edits.map((e) => [e.id, e]))
  // An undo is a live revert of an op (not of another revert: reverting a
  // redo would be an undo again, and that path is the undo button's).
  const lastUndo =
    [...session].reverse().find((e) => {
      if (e.op !== 'revert' || e.status === 'reverted' || !e.reverts) return false
      const target = byId.get(e.reverts)
      return target !== undefined && target.op !== 'revert' && target.status === 'reverted'
    }) ?? null
  const editedSince = lastUndo !== null && session.some((e) => e.op !== 'revert' && e.seq > lastUndo.seq)
  return { undo, redo: editedSince ? null : lastUndo }
}
