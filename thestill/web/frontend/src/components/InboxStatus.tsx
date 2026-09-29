import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { InboxEntry, InboxState } from '../api/types'
import { useInboxEntry, useSendToInbox, useSetInboxState } from '../hooks/useApi'
import Button, { InboxFilledIcon, InboxIcon } from './Button'
import { formatDeliveredDate, SAVED_VIEW_HREF } from '../utils/inbox'

// Spec #88. Delivery is immutable, mail-style: nothing here moves a row or
// changes when it arrived. Every action is an in-place state transition
// through the existing state endpoint. The import dialog and the episode
// page share this one presentation so the two cannot drift.

const STATE_SENTENCE: Record<InboxState, string> = {
  unread: "You haven't read it yet.",
  read: "You've read it.",
  saved: "It's in your saved items.",
  dismissed: "You dismissed it, so it's hidden from the main list.",
}

// The one in-place action offered for each state; ``saved`` has none.
const STATE_ACTION: Record<InboxState, { label: string; to: InboxState } | null> = {
  unread: { label: 'Save for later', to: 'saved' },
  read: { label: 'Save for later', to: 'saved' },
  saved: null,
  dismissed: { label: 'Restore to inbox', to: 'unread' },
}

interface InboxStatePanelProps {
  entry: InboxEntry
  /** Called after a successful transition with the new state. */
  onChanged?: (state: InboxState) => void
}

/** "Delivered 11 May. You've read it." plus the state's one action. */
export function InboxStatePanel({ entry, onChanged }: InboxStatePanelProps) {
  const { mutate, isPending } = useSetInboxState()
  const [error, setError] = useState<string | null>(null)
  const action = STATE_ACTION[entry.state]

  return (
    <div className="space-y-2" data-testid="inbox-state-panel">
      <p className="text-sm text-gray-700">
        Delivered {formatDeliveredDate(entry.delivered_at)}. {STATE_SENTENCE[entry.state]}
        {entry.state === 'saved' && (
          <>
            {' '}
            <Link to={SAVED_VIEW_HREF} className="text-primary-600 hover:underline">
              View saved
            </Link>
          </>
        )}
      </p>
      {action && (
        <Button
          variant="secondary"
          size="sm"
          isLoading={isPending}
          onClick={() => {
            setError(null)
            mutate(
              { episodeId: entry.episode_id, state: action.to },
              {
                onSuccess: () => onChanged?.(action.to),
                onError: (err: Error) => setError(err.message),
              },
            )
          }}
        >
          {action.label}
        </Button>
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  )
}

/**
 * Episode-page inbox control (spec #88), shown to every user as one 44 px icon
 * in the episode action row (spec #76 §3.2) — a full-width row here pushed the
 * tabs below the first phone viewport. With no row, a tap sends the episode
 * (an ``ad_hoc`` row; the pipeline starts when it has not been processed).
 * With a row, the icon is filled and a tap opens the row's state and its one
 * in-place action as a popover, which takes no layout space.
 */
export function InboxActionButton({ episodeId }: { episodeId: string }) {
  const { data, isLoading, isError } = useInboxEntry(episodeId)
  const { mutate: send, isPending } = useSendToInbox()
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointer = (e: MouseEvent | TouchEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('touchstart', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('touchstart', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Unknown is not "not in inbox": render nothing rather than guess.
  if (isLoading || isError || !data) return null

  const entry = data.entry
  const label = entry ? 'In your inbox' : 'Send to my inbox'

  return (
    <div ref={wrapRef} className="relative" data-testid="inbox-action">
      <Button
        variant="secondary"
        size="icon"
        icon={entry ? <InboxFilledIcon /> : <InboxIcon />}
        isLoading={isPending}
        aria-label={label}
        title={label}
        aria-haspopup={entry ? 'dialog' : undefined}
        aria-expanded={entry ? open : undefined}
        className={entry ? 'text-primary-700' : ''}
        onClick={() => {
          if (entry) {
            setOpen((o) => !o)
            return
          }
          setError(null)
          send(episodeId, {
            onSuccess: () => setOpen(true),
            onError: (err: Error) => {
              setError(err.message)
              setOpen(true)
            },
          })
        }}
      />
      {open && (entry || error) && (
        <div
          role="dialog"
          aria-label="Inbox status"
          className="absolute right-0 top-full z-30 mt-2 w-72 max-w-[calc(100vw-2rem)] rounded-lg border border-gray-200 bg-white px-4 py-3 text-left shadow-lg"
        >
          {entry && (
            <>
              <p className="text-sm font-medium text-gray-900">In your inbox</p>
              <div className="mt-1">
                <InboxStatePanel entry={entry} />
              </div>
            </>
          )}
          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>
      )}
    </div>
  )
}
