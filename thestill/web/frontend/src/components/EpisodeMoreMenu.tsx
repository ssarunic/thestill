import { useState, type ReactNode } from 'react'
import { useDismissablePopover } from '../hooks/useDismissablePopover'
import { useShareAction } from '../hooks/useShareAction'
import Button, { CheckIcon, LinkIcon, MoreIcon, PlayIcon, ShareIcon } from './Button'

interface EpisodeMoreMenuProps {
  shareTitle: string
  shareUrl: string
  /** The episode has a resumable position: offer "Play from beginning". */
  canPlayFromStart: boolean
  onPlayFromStart: () => void
  played: boolean
  /** Mark as played (true) or unplayed (false). Rejects when the write fails. */
  onMarkPlayed: (played: boolean) => Promise<void>
}

function MenuItem({ icon, onSelect, children }: { icon: ReactNode; onSelect: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onSelect}
      className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm text-gray-700 hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
    >
      <span className="h-4 w-4 shrink-0 text-gray-500" aria-hidden="true">
        {icon}
      </span>
      {children}
    </button>
  )
}

/**
 * Spec #90 — the episode action row's ⋯ menu, Spotify-style: Share, Play
 * from beginning and Mark as played / unplayed. It takes the Share icon's
 * slot, so the row keeps its four 44 px slots at 320 px (spec #76 §3.2).
 */
export default function EpisodeMoreMenu({
  shareTitle,
  shareUrl,
  canPlayFromStart,
  onPlayFromStart,
  played,
  onMarkPlayed,
}: EpisodeMoreMenuProps) {
  const { open, setOpen, wrapRef } = useDismissablePopover()
  const { share, canShare, label: shareLabel } = useShareAction(shareTitle, shareUrl)
  const [error, setError] = useState<string | null>(null)

  const select = (action: () => void) => () => {
    setOpen(false)
    action()
  }

  return (
    <div ref={wrapRef} className="relative" data-testid="episode-more-menu">
      <Button
        variant="secondary"
        size="icon"
        icon={<MoreIcon />}
        aria-label="More actions"
        title="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          setError(null)
          setOpen((o) => !o)
        }}
      />
      {open && (
        <div
          role="menu"
          aria-label="More actions"
          className="absolute right-0 top-full z-30 mt-2 w-60 max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
        >
          <MenuItem icon={canShare ? <ShareIcon /> : <LinkIcon />} onSelect={select(() => void share())}>
            {shareLabel}
          </MenuItem>
          {canPlayFromStart && (
            <MenuItem icon={<PlayIcon />} onSelect={select(onPlayFromStart)}>
              Play from beginning
            </MenuItem>
          )}
          <MenuItem
            icon={<CheckIcon />}
            onSelect={() => {
              onMarkPlayed(!played).then(
                () => setOpen(false),
                () => setError('Could not update. Try again.'),
              )
            }}
          >
            {played ? 'Mark as unplayed' : 'Mark as played'}
          </MenuItem>
          {error && (
            <p role="alert" className="px-4 py-2 text-sm text-red-600">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
