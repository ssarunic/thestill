import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import Button, { RedoIcon, UndoIcon } from '../Button'
import type { SaveStatus } from '../../hooks/useTranscriptEditor'
import type { TranscriptEditConflict, TranscriptEdit } from '../../api/types'
import ConflictsList from './ConflictsList'

// Spec #110 §5.2 — the editor screen's top bar: Back, the episode, undo /
// redo (⌘Z, ⇧⌘Z), the save status, the live edit count, the conflicts chip
// and Finish editing as the one primary button.

interface TranscriptEditorBarProps {
  backTo: string
  title: string
  subtitle?: string | null
  saveStatus: SaveStatus
  saveError: string | null
  canUndo: boolean
  canRedo: boolean
  busy: boolean
  conflicts: TranscriptEditConflict[]
  edits: TranscriptEdit[]
  /** A transcribe or clean is in flight: writes are refused (spec #110 §2). */
  locked: boolean
  finishing: boolean
  onUndo: () => void
  onRedo: () => void
  onDropConflict: (editId: string) => void
  onFinish: () => void
}

function SaveIndicator({ status, error }: { status: SaveStatus; error: string | null }) {
  if (status === 'saving') {
    return (
      <span role="status" className="inline-flex items-center gap-1.5 text-xs text-muted">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-primary-200 border-t-primary-600" />
        Saving…
      </span>
    )
  }
  if (status === 'error') {
    return (
      <span role="alert" className="inline-flex items-center gap-1.5 text-xs text-red-600">
        <span className="h-2 w-2 rounded-full bg-red-500" aria-hidden="true" />
        {error ?? 'Could not save'}
      </span>
    )
  }
  if (status === 'stale') {
    return (
      <span role="alert" className="inline-flex items-center gap-1.5 text-xs text-amber-800">
        <span className="h-2 w-2 rounded-full bg-amber-500" aria-hidden="true" />
        {error ?? 'Reloaded'}
      </span>
    )
  }
  return (
    <span role="status" className="inline-flex items-center gap-1.5 text-xs text-muted">
      <span className="h-2 w-2 rounded-full bg-green-500" aria-hidden="true" />
      Saved
    </span>
  )
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
}

export default function TranscriptEditorBar({
  backTo,
  title,
  subtitle,
  saveStatus,
  saveError,
  canUndo,
  canRedo,
  busy,
  conflicts,
  edits,
  locked,
  finishing,
  onUndo,
  onRedo,
  onDropConflict,
  onFinish,
}: TranscriptEditorBarProps) {
  const [showConflicts, setShowConflicts] = useState(false)

  // ⌘Z / ⇧⌘Z outside text fields (a textarea keeps its own undo).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 'z' || isTyping(e.target)) return
      e.preventDefault()
      if (e.shiftKey) {
        if (canRedo) onRedo()
      } else if (canUndo) {
        onUndo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [canUndo, canRedo, onUndo, onRedo])

  const liveEdits = edits.filter((e) => e.status === 'applied').length

  return (
    <header data-testid="transcript-editor-bar" className="border-b border-hairline bg-surface px-4 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Link
          to={backTo}
          className="inline-flex min-h-[36px] items-center gap-1 rounded-lg px-2 text-sm text-gray-600 hover:bg-gray-100 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
          aria-label="Back to the episode"
        >
          <span aria-hidden="true">←</span> Back
        </Link>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink">{title}</p>
          {subtitle && <p className="truncate text-xs text-muted">{subtitle}</p>}
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <SaveIndicator status={saveStatus} error={saveError} />
          <span className="text-xs text-muted">
            {liveEdits} edit{liveEdits === 1 ? '' : 's'}
          </span>
          <Button
            variant="ghost"
            size="sm"
            icon={<UndoIcon />}
            aria-label="Undo"
            title="Undo (⌘Z)"
            disabled={!canUndo || busy || locked}
            onClick={onUndo}
          />
          <Button
            variant="ghost"
            size="sm"
            icon={<RedoIcon />}
            aria-label="Redo"
            title="Redo (⇧⌘Z)"
            disabled={!canRedo || busy || locked}
            onClick={onRedo}
          />
          {conflicts.length > 0 && (
            <button
              type="button"
              aria-pressed={showConflicts}
              onClick={() => setShowConflicts((v) => !v)}
              className="inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-800 hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
            >
              {conflicts.length} conflict{conflicts.length === 1 ? '' : 's'}
            </button>
          )}
          <Button variant="primary" size="sm" onClick={onFinish} isLoading={finishing} disabled={busy || locked}>
            Finish editing
          </Button>
        </div>
      </div>
      {locked && (
        <p role="status" className="mt-2 text-xs text-amber-800">
          A transcribe or clean is running for this episode; editing resumes when it finishes.
        </p>
      )}
      {showConflicts && conflicts.length > 0 && (
        <ConflictsList conflicts={conflicts} edits={edits} onDrop={onDropConflict} className="mt-3" />
      )}
    </header>
  )
}
