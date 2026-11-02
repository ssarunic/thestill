import Button, { PauseIcon, PlayIcon } from '../Button'
import { formatClock } from './time'

// Spec #110 §5.5 — the transport row of the editor's bottom dock: bound to
// the app's player so follow and karaoke keep working and the mini player
// is never needed.

const RATES = [0.8, 1, 1.25, 1.5, 2]

interface TransportDockProps {
  isPlaying: boolean
  isLoading: boolean
  currentTime: number
  duration: number
  playbackRate: number
  availableRates: number[] | null
  /** Playing our local copy of the audio (spec #110 §5.6). */
  localAudio: boolean
  onToggle: () => void
  onSkip: (deltaSeconds: number) => void
  onRate: (rate: number) => void
}

export default function TransportDock({
  isPlaying,
  isLoading,
  currentTime,
  duration,
  playbackRate,
  availableRates,
  localAudio,
  onToggle,
  onSkip,
  onRate,
}: TransportDockProps) {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="transport-dock">
      <Button
        variant="primary"
        size="iconSm"
        icon={isPlaying ? <PauseIcon /> : <PlayIcon />}
        aria-label={isPlaying ? 'Pause' : 'Play'}
        title={isPlaying ? 'Pause (Space)' : 'Play (Space)'}
        isLoading={isLoading}
        onClick={onToggle}
      />
      <Button variant="ghost" size="sm" onClick={() => onSkip(-5)} aria-label="Back 5 seconds" title="Back 5 seconds (J)">
        −5s
      </Button>
      <Button variant="ghost" size="sm" onClick={() => onSkip(5)} aria-label="Forward 5 seconds" title="Forward 5 seconds (L)">
        +5s
      </Button>
      <select
        value={playbackRate}
        onChange={(e) => onRate(Number(e.target.value))}
        aria-label="Playback speed"
        className="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
      >
        {RATES.map((rate) => (
          <option key={rate} value={rate} disabled={availableRates !== null && !availableRates.includes(rate)}>
            {rate}×
          </option>
        ))}
      </select>
      <span className="font-mono text-xs tabular-nums text-gray-700" aria-live="off">
        {formatClock(currentTime, true)} <span className="text-gray-400">/ {formatClock(duration)}</span>
      </span>
      {localAudio && (
        <span className="rounded bg-primary-50 px-1.5 py-0.5 text-[11px] text-primary-800" title="Playing our downloaded copy, the file the timestamps were made from">
          local audio
        </span>
      )}
    </div>
  )
}
