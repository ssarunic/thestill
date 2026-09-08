import { usePersistedBoolean } from '../hooks/useAutoScrollFollow'
import { formatClock } from '../utils/formatClock'

export interface ScrubberTick {
  /** Position as a fraction of the duration, 0..1. */
  at: number
  /** Tailwind dot colour class (entityStyle(type).dot). */
  colorClass: string
  label: string
  onSelect: () => void
}

interface NowPlayingScrubberProps {
  currentTime: number
  duration: number
  onSeek: (seconds: number) => void
  /** Spec #72 §2 entity ticks (phase 2b); omitted = no tick row. */
  ticks?: ScrubberTick[]
}

const REMAINING_KEY = 'thestill:player:remaining'

/**
 * Spec #72 §2 — the full-width scrubber: a 44 px hit area over a 4 px track,
 * elapsed on the left, total or remaining on the right (tap toggles,
 * persisted), and an optional tick row beneath the track. Disabled with
 * `--:--` while the duration is unknown.
 */
export default function NowPlayingScrubber({ currentTime, duration, onSeek, ticks }: NowPlayingScrubberProps) {
  const [showRemaining, setShowRemaining] = usePersistedBoolean(REMAINING_KEY, false)
  const hasDuration = duration > 0 && Number.isFinite(duration)
  const progress = hasDuration ? Math.min(1, Math.max(0, currentTime / duration)) : 0
  const rightLabel = !hasDuration
    ? '--:--'
    : showRemaining
      ? `-${formatClock(Math.max(0, duration - currentTime))}`
      : formatClock(duration)

  return (
    <div className="space-y-1">
      <div className="relative h-11">
        <input
          type="range"
          min={0}
          max={hasDuration ? duration : 100}
          step={1}
          value={hasDuration ? currentTime : 0}
          onChange={(e) => onSeek(Number(e.target.value))}
          disabled={!hasDuration}
          aria-label="Seek"
          aria-valuetext={hasDuration ? `${formatClock(currentTime)} of ${formatClock(duration)}` : 'Duration unknown'}
          className="absolute inset-0 h-11 w-full cursor-pointer appearance-none bg-transparent disabled:cursor-not-allowed [&::-webkit-slider-runnable-track]:h-1 [&::-webkit-slider-runnable-track]:rounded-full [&::-moz-range-track]:h-1 [&::-moz-range-track]:rounded-full [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:-mt-1.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-accent [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:bg-accent"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute left-0 right-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-gray-200"
        >
          <div className="h-full rounded-full bg-accent" style={{ width: `${progress * 100}%` }} />
        </div>
      </div>

      {ticks && ticks.length > 0 && hasDuration && (
        <div className="relative h-3" data-testid="scrubber-ticks">
          {ticks.map((tick, i) => (
            <button
              key={i}
              type="button"
              onClick={tick.onSelect}
              title={tick.label}
              aria-label={tick.label}
              style={{ left: `${Math.min(100, Math.max(0, tick.at * 100))}%` }}
              className={`absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ${tick.colorClass} hover:scale-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400`}
            />
          ))}
        </div>
      )}

      <div className="flex items-center justify-between text-xs tabular-nums text-muted">
        <span>{formatClock(currentTime)}</span>
        <button
          type="button"
          onClick={() => setShowRemaining(!showRemaining)}
          disabled={!hasDuration}
          aria-label={showRemaining ? 'Showing time remaining; switch to total duration' : 'Showing total duration; switch to time remaining'}
          className="rounded px-1 hover:text-ink disabled:cursor-default disabled:hover:text-muted"
        >
          {rightLabel}
        </button>
      </div>
    </div>
  )
}
