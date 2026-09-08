import { formatRateLabel, nextRate } from '../utils/playbackRate'

interface NowPlayingSpeedControlProps {
  rate: number
  /** Rates the active engine accepts; null = unrestricted (spec #72 §5). */
  availableRates: number[] | null
  onChange: (rate: number) => void
  className?: string
}

/**
 * Spec #72 §5 — playback speed as one compact chip that shows the current
 * rate and steps to the next on tap (`0.8× → 1× → 1.2× → 1.5× → 2× → 0.8×`).
 * A set-and-forget preference does not earn a segmented control; the chip
 * takes one slot in the sheet's utility row. On the YouTube engine, rates
 * the video does not accept are skipped.
 */
export default function NowPlayingSpeedControl({ rate, availableRates, onChange, className = '' }: NowPlayingSpeedControlProps) {
  const label = formatRateLabel(rate)
  return (
    <button
      type="button"
      onClick={() => onChange(nextRate(rate, availableRates))}
      aria-label={`Speed ${label}`}
      title="Change playback speed"
      className={className}
    >
      <span aria-hidden="true" className="flex h-6 items-center text-base font-semibold tabular-nums leading-none">
        {label}
      </span>
      <span aria-hidden="true" className="text-[11px] font-medium leading-none">
        Speed
      </span>
    </button>
  )
}
