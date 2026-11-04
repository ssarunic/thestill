import { useLayoutEffect, useMemo, useRef, useState, type WheelEvent } from 'react'
import type { AnnotatedSegment, TranscriptSpeaker } from '../../api/types'
import {
  DEFAULT_SPAN_SECONDS,
  clampWindow,
  followDecision,
  ticksFor,
  type FollowState,
  timeToX,
  xToTime,
  zoomWindow,
} from './laneMath'
import { formatClock } from './time'

// Spec #110 §5.5 — one lane per voice, every segment a block on a shared
// time axis, the playhead as a line. This is what makes a stray turn (three
// SPEAKER_03 blocks inside Ada's run) visible before any listening.
// Scroll pans, ⌘/Ctrl-scroll zooms, and the window follows the playhead
// while playing.

const LANE_HEIGHT = 22
const AXIS_HEIGHT = 18
const LABEL_WIDTH = 112

interface LaneTimelineProps {
  speakers: TranscriptSpeaker[]
  segments: AnnotatedSegment[]
  labelsBySegmentId: Map<number, string>
  colors: Map<string, string>
  /** Playback offset: segment times are transcript time, the axis is engine time. */
  offset: number
  duration: number
  currentTime: number
  isPlaying: boolean
  selectedId: number | null
  focusLabel: string | null
  onSelect: (segmentId: number) => void
  onSeek: (seconds: number) => void
}

export default function LaneTimeline({
  speakers,
  segments,
  labelsBySegmentId,
  colors,
  offset,
  duration,
  currentTime,
  isPlaying,
  selectedId,
  focusLabel,
  onSelect,
  onSeek,
}: LaneTimelineProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [width, setWidth] = useState(800)
  const [window_, setWindow] = useState({ start: 0, span: DEFAULT_SPAN_SECONDS })
  const total = Math.max(duration, 1)

  useLayoutEffect(() => {
    const el = svgRef.current
    if (!el) return
    const measure = () => setWidth(Math.max(200, el.clientWidth || 800))
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // Follow the playhead while playing, and re-anchor on a new selection.
  // Both are render-time adjustments (no effect, no extra commit): the
  // window only moves when the playhead leaves its middle or the selected
  // row changes to one outside it. A manual pan or zoom suspends following
  // until the playhead, once inside the chosen window, leaves it
  // (laneMath.followDecision), so the timeline never snaps back under the
  // wheel.
  const [follow, setFollow] = useState<FollowState>({ panned: false, wasInside: true })
  const inWindow = (at: number) => at >= window_.start && at <= window_.start + window_.span
  if (isPlaying) {
    const decision = followDecision(currentTime, window_.start, window_.span, total, follow)
    if (decision.start !== null) setWindow((w) => ({ ...w, start: decision.start as number }))
    if (decision.next.panned !== follow.panned || decision.next.wasInside !== follow.wasInside) setFollow(decision.next)
  }
  const selected = useMemo(() => segments.find((s) => s.id === selectedId) ?? null, [segments, selectedId])
  const [anchoredId, setAnchoredId] = useState<number | null>(null)
  if (selectedId !== anchoredId) {
    setAnchoredId(selectedId)
    if (selected) {
      const at = selected.start + offset
      if (!inWindow(at)) {
        setFollow({ panned: false, wasInside: true })
        setWindow((w) => ({ ...w, start: clampWindow(at - w.span * 0.3, w.span, total) }))
      }
    }
  }

  const onWheel = (e: WheelEvent<SVGSVGElement>) => {
    e.preventDefault()
    setFollow((f) => (f.panned ? f : { panned: true, wasInside: inWindow(currentTime) }))
    if (e.metaKey || e.ctrlKey) {
      const rect = e.currentTarget.getBoundingClientRect()
      const anchor = xToTime(e.clientX - rect.left, window_.start, window_.span, width)
      setWindow((w) => zoomWindow(w.start, w.span, e.deltaY > 0 ? 1.25 : 0.8, anchor, total))
      return
    }
    const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY
    setWindow((w) => ({ ...w, start: clampWindow(w.start + (delta / width) * w.span, w.span, total) }))
  }

  const bySpeaker = useMemo(() => {
    const map = new Map<string, AnnotatedSegment[]>()
    for (const segment of segments) {
      const label = labelsBySegmentId.get(segment.id)
      if (!label) continue
      const list = map.get(label) ?? []
      list.push(segment)
      map.set(label, list)
    }
    return map
  }, [segments, labelsBySegmentId])

  const height = speakers.length * LANE_HEIGHT + AXIS_HEIGHT
  const visible = (segment: AnnotatedSegment) =>
    segment.end + offset >= window_.start && segment.start + offset <= window_.start + window_.span
  const playheadX = timeToX(currentTime, window_.start, window_.span, width)

  return (
    <div className="flex" data-testid="lane-timeline">
      <div className="shrink-0 text-xs" style={{ width: LABEL_WIDTH }}>
        {speakers.map((speaker) => (
          <div
            key={speaker.label}
            className={`flex items-center gap-1.5 truncate pr-2 ${focusLabel && focusLabel !== speaker.label ? 'opacity-40' : ''}`}
            style={{ height: LANE_HEIGHT }}
            title={speaker.label}
          >
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: colors.get(speaker.label) ?? '#4b5563' }} />
            <span className="truncate text-ink">{speaker.name ?? speaker.label}</span>
          </div>
        ))}
        <div style={{ height: AXIS_HEIGHT }} />
      </div>
      <svg
        ref={svgRef}
        role="img"
        aria-label="Segments per voice on the episode timeline"
        className="min-w-0 flex-1 select-none"
        height={height}
        onWheel={onWheel}
        onClick={(e) => {
          if (e.target !== e.currentTarget) return
          const rect = e.currentTarget.getBoundingClientRect()
          onSeek(xToTime(e.clientX - rect.left, window_.start, window_.span, width))
        }}
      >
        {speakers.map((speaker, lane) => (
          <g key={speaker.label} transform={`translate(0 ${lane * LANE_HEIGHT})`}>
            <line x1={0} x2={width} y1={LANE_HEIGHT - 0.5} y2={LANE_HEIGHT - 0.5} className="stroke-hairline" strokeWidth={1} />
            {(bySpeaker.get(speaker.label) ?? []).filter(visible).map((segment) => {
              const x = timeToX(segment.start + offset, window_.start, window_.span, width)
              const w = Math.max(2, timeToX(segment.end + offset, window_.start, window_.span, width) - x)
              const dim = focusLabel !== null && focusLabel !== speaker.label
              return (
                <rect
                  key={segment.id}
                  data-testid={`lane-block-${segment.id}`}
                  x={x}
                  y={4}
                  width={w}
                  height={LANE_HEIGHT - 8}
                  rx={3}
                  fill={colors.get(speaker.label) ?? '#4b5563'}
                  fillOpacity={segment.id === selectedId ? 1 : dim ? 0.2 : 0.55}
                  stroke={segment.id === selectedId ? '#1a365d' : 'none'}
                  strokeWidth={1.5}
                  className="cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation()
                    onSelect(segment.id)
                  }}
                >
                  <title>{`${speaker.name ?? speaker.label} · ${formatClock(segment.start + offset)}`}</title>
                </rect>
              )
            })}
          </g>
        ))}
        <g transform={`translate(0 ${speakers.length * LANE_HEIGHT})`}>
          {ticksFor(window_.start, window_.span).map((t) => {
            const x = timeToX(t, window_.start, window_.span, width)
            return (
              <g key={t}>
                <line x1={x} x2={x} y1={0} y2={4} className="stroke-gray-400" strokeWidth={1} />
                <text x={x + 3} y={13} className="fill-gray-500" fontSize={10} fontFamily="ui-monospace, monospace">
                  {formatClock(t)}
                </text>
              </g>
            )
          })}
        </g>
        {playheadX >= 0 && playheadX <= width && (
          <line
            data-testid="lane-playhead"
            x1={playheadX}
            x2={playheadX}
            y1={0}
            y2={height}
            stroke="#1a365d"
            strokeWidth={1.5}
            pointerEvents="none"
          />
        )}
      </svg>
    </div>
  )
}
