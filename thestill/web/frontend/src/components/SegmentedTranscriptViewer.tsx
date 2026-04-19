import { useMemo } from 'react'
import type { AnnotatedSegment, AnnotatedTranscriptDump } from '../api/types'
import { getSpeakerColor } from '../utils/speakerColors'

interface SegmentedTranscriptViewerProps {
  transcript: AnnotatedTranscriptDump
}

function formatTimestamp(seconds: number): string {
  const total = Math.floor(seconds)
  const hh = Math.floor(total / 3600)
  const mm = Math.floor((total % 3600) / 60)
  const ss = total % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  return hh > 0 ? `${pad(hh)}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`
}

function AdBreak({ segment, offset }: { segment: AnnotatedSegment; offset: number }) {
  const sponsor = segment.sponsor ? ` — ${segment.sponsor}` : ''
  return (
    <div className="my-5 border-l-4 border-amber-400 bg-amber-50/70 px-4 py-3 rounded-r-md">
      <div className="flex items-center gap-3 text-amber-800">
        <span className="font-mono text-[11px] tabular-nums text-amber-700/80">
          {formatTimestamp(segment.start + offset)}
        </span>
        <span className="text-xs font-semibold uppercase tracking-wider">
          Ad break{sponsor}
        </span>
      </div>
    </div>
  )
}

function ContentSegment({ segment, offset }: { segment: AnnotatedSegment; offset: number }) {
  const speaker = segment.speaker ?? 'Unknown'
  return (
    <div className="group -mx-2 px-2 py-2.5 rounded-lg transition-colors sm:-mx-3 sm:px-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-1.5">
        <span className={`font-sans text-sm font-semibold tracking-tight ${getSpeakerColor(speaker)}`}>
          {speaker}
        </span>
        <span className="font-mono text-[11px] tabular-nums text-gray-400">
          {formatTimestamp(segment.start + offset)}
        </span>
      </div>
      <p className="text-gray-800 pl-4 border-l-2 border-gray-200 text-[15px] leading-[1.75] sm:text-[17px]">
        {segment.text}
      </p>
    </div>
  )
}

export default function SegmentedTranscriptViewer({ transcript }: SegmentedTranscriptViewerProps) {
  const offset = transcript.playback_time_offset_seconds ?? 0
  const visibleSegments = useMemo(
    () => transcript.segments.filter((seg) => seg.kind !== 'filler'),
    [transcript.segments],
  )

  if (visibleSegments.length === 0) {
    return (
      <div className="text-center py-16 min-h-[300px] border border-dashed border-gray-200 rounded-lg bg-gray-50/50">
        <p className="text-gray-600 font-medium">No content segments available</p>
        <p className="text-sm text-gray-400 mt-1">
          This transcript's segments have all been marked as filler.
        </p>
      </div>
    )
  }

  return (
    <div className="transcript-content leading-relaxed space-y-1.5">
      {visibleSegments.map((segment) =>
        segment.kind === 'ad_break' ? (
          <AdBreak key={segment.id} segment={segment} offset={offset} />
        ) : (
          <ContentSegment key={segment.id} segment={segment} offset={offset} />
        ),
      )}
    </div>
  )
}
