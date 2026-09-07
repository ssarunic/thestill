import Artwork from './Artwork'
import Button, { PauseIcon, PlayIcon } from './Button'

/** What the reader reports upward once its title scrolls away (spec #76 §3.7). */
export interface CollapsedHeaderState {
  title: string
  artworkUrl: string | null
  isPlaying: boolean
  isLoading: boolean
  onTogglePlay: () => void
}

interface CollapsedEpisodeBarProps {
  state: CollapsedHeaderState
  className?: string
}

/**
 * The 56 px collapsed header: 32 px artwork, one-line title, a play/pause
 * control drawn at 36 px inside a 44 px hit area. Presentational — both
 * hosts render it in their own chrome. Fades in; no motion under
 * ``prefers-reduced-motion``.
 */
export default function CollapsedEpisodeBar({ state, className = '' }: CollapsedEpisodeBarProps) {
  const { title, artworkUrl, isPlaying, isLoading, onTogglePlay } = state
  return (
    <div
      className={`flex h-14 min-w-0 items-center gap-3 animate-[fade-in_150ms_ease-out] motion-reduce:animate-none ${className}`}
      data-testid="collapsed-episode-bar"
    >
      <Artwork role="bar" sources={[artworkUrl]} />
      <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{title}</span>
      <Button
        size="iconSm"
        variant="primary"
        icon={isPlaying ? <PauseIcon /> : <PlayIcon />}
        isLoading={isLoading && !isPlaying}
        onClick={onTogglePlay}
        aria-label={isPlaying ? 'Pause' : 'Play'}
      >
        <span className="sr-only">{isPlaying ? 'Pause' : 'Play'}</span>
      </Button>
    </div>
  )
}
