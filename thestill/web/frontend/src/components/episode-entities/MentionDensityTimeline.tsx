import { useMemo } from 'react'
import type { EpisodeEntity } from '../../api/types'
import { entityStyle } from '../../utils/entityColors'

// Spec #28 §5.2 — "Mention density timeline (left of the audio
// scrubber): thin vertical strip alongside <FloatingPlayer/> showing
// one row per top-N entity (configurable, default N=5). Each row
// plots dots at each mention's start_ms along the episode duration.
// Click a dot → seek + scroll the corresponding transcript segment
// into view."
//
// We render this as a fixed-position panel that docks above the
// MiniPlayer (which lives at `fixed bottom-0` with `sm:left-16
// lg:left-64`). The component is mounted by EpisodeDetail only when
// the player is on the current episode — that's when "left of the
// scrubber" makes sense.

export interface MentionDensityTimelineProps {
  // The N top entities to plot rows for. Caller picks which set
  // (typically the same top-N that drives the strip).
  entities: EpisodeEntity[]
  durationSeconds: number
  onSeek: (seconds: number) => void
}

const TOP_N = 5

export default function MentionDensityTimeline({
  entities,
  durationSeconds,
  onSeek,
}: MentionDensityTimelineProps) {
  const top = useMemo(
    () => entities.slice().sort((a, b) => b.mention_count - a.mention_count).slice(0, TOP_N),
    [entities],
  )

  if (top.length === 0 || durationSeconds <= 0) {
    return null
  }

  return (
    <div
      // Positioned just above the MiniPlayer (which is `bottom-0 h-16`-ish).
      // Hidden on mobile — the spec calls for this to live "left of the
      // audio scrubber", which only exists on screens wide enough to
      // have a scrubber and sidebar.
      className="pointer-events-none fixed bottom-20 left-2 right-2 z-10 hidden md:block sm:left-20 lg:left-72"
      data-testid="mention-density-timeline"
      aria-label="Mention timeline"
    >
      <div className="pointer-events-auto rounded-lg border border-gray-200 bg-white/95 px-3 py-2 shadow-sm backdrop-blur-sm">
        <div className="space-y-1">
          {top.map((item) => {
            const style = entityStyle(item.entity.type)
            return (
              <div key={item.entity.id} className="flex items-center gap-2 text-xs">
                <span
                  className="w-24 truncate text-right text-gray-600"
                  title={item.entity.canonical_name}
                >
                  {item.entity.canonical_name}
                </span>
                <span className="relative h-3 flex-1 rounded bg-gray-50">
                  {item.mentions.map((m) => {
                    const pct = (m.start_ms / 1000 / durationSeconds) * 100
                    if (pct < 0 || pct > 100) return null
                    return (
                      <button
                        key={m.id}
                        type="button"
                        onClick={() => onSeek(m.start_ms / 1000)}
                        title={`Seek to ${formatTimestamp(m.start_ms)}`}
                        aria-label={`Mention of ${item.entity.canonical_name} at ${formatTimestamp(m.start_ms)}`}
                        // Each dot is a small button; absolute-positioned
                        // along the row by percentage of episode duration.
                        // Negative margin centers the dot on the percent.
                        style={{ left: `${pct}%` }}
                        className={`absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full ${style.dot} hover:scale-150 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary-400`}
                      />
                    )
                  })}
                </span>
                <span className="w-6 text-right text-[10px] tabular-nums text-gray-400">
                  {item.mention_count}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function formatTimestamp(ms: number): string {
  const total = Math.floor(ms / 1000)
  const hh = Math.floor(total / 3600)
  const mm = Math.floor((total % 3600) / 60)
  const ss = total % 60
  if (hh > 0) {
    return `${hh}:${mm.toString().padStart(2, '0')}:${ss.toString().padStart(2, '0')}`
  }
  return `${mm}:${ss.toString().padStart(2, '0')}`
}
