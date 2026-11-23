import { track } from '../analytics/client'
import { usePlayer } from '../contexts/PlayerContext'

/**
 * Spec #116 §6.2 — "Skip ahead ⏭" in the mini player and the Now Playing
 * sheet. It is there only while an ad plays and the timeline map knows
 * where the episode resumes after it, and gone when the ad ends. One tap
 * jumps past the ad, and past any ad right after it.
 */
export default function SkipAheadButton({ size = 'sm' }: { size?: 'sm' | 'md' }) {
  const { skipAheadAvailable, skipAhead } = usePlayer()
  if (!skipAheadAvailable) return null
  const onClick = () => {
    const skipped = skipAhead()
    if (skipped !== null) track('skip_ahead', { seconds: Math.round(skipped) })
  }
  const sizing = size === 'md' ? 'h-9 px-4 text-sm' : 'h-8 px-3 text-xs'
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid="skip-ahead"
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full bg-accent font-semibold text-accent-contrast hover:bg-primary-800 active:bg-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${sizing}`}
    >
      Skip ahead
      <svg className="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5 5.5v13l10-6.5z" />
        <rect x="16" y="5" width="3" height="14" rx="1" />
      </svg>
    </button>
  )
}
