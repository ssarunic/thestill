import { formatRateLabel, RATE_OPTIONS } from '../utils/playbackRate'

interface NowPlayingSpeedControlProps {
  rate: number
  /** Rates the active engine accepts; null = unrestricted (spec #72 §5). */
  availableRates: number[] | null
  onChange: (rate: number) => void
}

function nearlyEqual(a: number, b: number): boolean {
  return Math.abs(a - b) < 0.01
}

/**
 * Spec #72 §5 — segmented speed control over the fixed option set. On the
 * YouTube engine, options the video does not accept are disabled rather
 * than hidden, so the control keeps its shape.
 */
export default function NowPlayingSpeedControl({ rate, availableRates, onChange }: NowPlayingSpeedControlProps) {
  return (
    <div role="radiogroup" aria-label="Playback speed" className="flex w-full rounded-lg border border-hairline bg-page p-0.5">
      {RATE_OPTIONS.map((option) => {
        const selected = nearlyEqual(option, rate)
        const supported = !availableRates || availableRates.some((r) => nearlyEqual(r, option))
        return (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={!supported}
            title={supported ? undefined : 'Not available for this video'}
            onClick={() => onChange(option)}
            className={`min-h-[40px] min-w-[44px] flex-1 rounded-md px-1 text-sm font-medium tabular-nums transition-colors ${
              selected
                ? 'bg-accent text-accent-contrast shadow-sm'
                : 'text-gray-600 hover:bg-gray-100 hover:text-ink disabled:text-gray-300 disabled:hover:bg-transparent disabled:cursor-not-allowed'
            }`}
          >
            {formatRateLabel(option)}
          </button>
        )
      })}
    </div>
  )
}
