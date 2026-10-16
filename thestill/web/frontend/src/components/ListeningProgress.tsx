import type { ReactNode } from 'react'
import { CheckIcon } from './Button'

/**
 * Spec #90 — artwork with a 2 px listening-progress line along its bottom
 * edge. No extra height: the line sits inside the thumbnail (spec #73
 * density rules). Renders the artwork alone when there is no progress.
 */
export function ArtworkWithProgress({
  artwork,
  fraction,
  className = 'rounded-lg',
}: {
  artwork: ReactNode
  fraction: number | null
  /** Matches the artwork role's radius so the line is clipped to it. */
  className?: string
}) {
  if (fraction === null) return <>{artwork}</>
  return (
    <div className={`relative shrink-0 overflow-hidden ${className}`}>
      {artwork}
      <div aria-hidden="true" className="absolute inset-x-0 bottom-0 h-0.5 bg-black/25">
        <div className="h-full bg-primary-600" style={{ width: `${Math.round(fraction * 100)}%` }} />
      </div>
    </div>
  )
}

/** Inline ``N min left`` / ``✓ Played`` for a one-line metadata row. */
export function ListeningLabel({ label, played }: { label: string | null; played: boolean }) {
  if (played) {
    return (
      <span className="inline-flex items-center gap-1 whitespace-nowrap flex-shrink-0">
        <span className="h-3 w-3" aria-hidden="true">
          <CheckIcon />
        </span>
        Played
      </span>
    )
  }
  if (!label) return null
  return <span className="whitespace-nowrap flex-shrink-0">{label}</span>
}

/** Compact progress meter for the episode hero's eyebrow line. */
export function ListeningMeter({ fraction, label }: { fraction: number; label: string }) {
  const percent = Math.round(fraction * 100)
  return (
    <span
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      className="inline-block h-1 w-12 overflow-hidden rounded-full bg-gray-200 align-middle"
    >
      <span className="block h-full bg-primary-600" style={{ width: `${percent}%` }} />
    </span>
  )
}
