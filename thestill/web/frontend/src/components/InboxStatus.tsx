import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { InboxEntry, InboxState } from '../api/types'
import { useInboxEntry, useSendToInbox, useSetInboxState } from '../hooks/useApi'
import Button, { PlusIcon } from './Button'
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
 * Episode-page inbox control (spec #88), shown to every user. With no row it
 * is "Send to my inbox", which creates an ``ad_hoc`` row and starts the
 * pipeline when the episode has not been processed. With a row it shows the
 * row's state and the same in-place action as the import dialog.
 */
export function InboxActionButton({ episodeId }: { episodeId: string }) {
  const { data, isLoading, isError } = useInboxEntry(episodeId)
  const { mutate: send, isPending } = useSendToInbox()
  const [error, setError] = useState<string | null>(null)

  // A failed lookup must not masquerade as "not in your inbox": offering
  // Send on an error would be wrong half the time. Render nothing instead.
  if (isLoading || isError || !data) return null

  if (data.entry) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white px-4 py-3" data-testid="inbox-action">
        <p className="text-sm font-medium text-gray-900">In your inbox</p>
        <div className="mt-1">
          <InboxStatePanel entry={data.entry} />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-2" data-testid="inbox-action">
      <Button
        variant="secondary"
        icon={<PlusIcon />}
        isLoading={isPending}
        onClick={() => {
          setError(null)
          send(episodeId, { onError: (err: Error) => setError(err.message) })
        }}
      >
        Send to my inbox
      </Button>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  )
}
