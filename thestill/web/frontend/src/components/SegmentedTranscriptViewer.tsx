import { Fragment } from 'react'
import type { AnnotatedSegment, AnnotatedTranscriptDump } from '../api/types'

interface SegmentedTranscriptViewerProps {
  transcript: AnnotatedTranscriptDump
}

// Speaker-colour mapping mirrors ``TranscriptViewer.tsx``. Both
// components will render side-by-side during the transition and we want
// a given speaker to stay the same colour across them.
const speakerColors: Record<string, string> = {
  SPEAKER_00: 'text-blue-700',
  SPEAKER_01: 'text-purple-700',
  SPEAKER_02: 'text-green-700',
  SPEAKER_03: 'text-orange-700',
  SPEAKER_04: 'text-pink-700',
}

function getSpeakerColor(speaker: string): string {
  if (speakerColors[speaker]) return speakerColors[speaker]
  const hash = speaker.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0)
  const colors = [
    'text-blue-700',
    'text-purple-700',
    'text-green-700',
    'text-orange-700',
    'text-pink-700',
    'text-indigo-700',
    'text-red-700',
  ]
  return colors[hash % colors.length]
}

function formatTimestamp(seconds: number): string {
  const total = Math.floor(seconds)
  const hh = Math.floor(total / 3600)
  const mm = Math.floor((total % 3600) / 60)
  const ss = total % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  return hh > 0 ? `${pad(hh)}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`
}

function renderAdBreak(segment: AnnotatedSegment, key: string | number, offset: number) {
  const sponsor = segment.sponsor ? ` — ${segment.sponsor}` : ''
  return (
    <div
      key={key}
      className="my-4 border-l-4 border-amber-400 bg-amber-50 px-4 py-3 rounded-r"
    >
      <div className="flex items-center gap-2 text-sm font-medium text-amber-800">
        <span className="font-mono text-xs">[{formatTimestamp(segment.start + offset)}]</span>
        <span>Ad break{sponsor}</span>
      </div>
    </div>
  )
}

function renderContent(segment: AnnotatedSegment, key: string | number, offset: number) {
  const speaker = segment.speaker ?? 'Unknown'
  return (
    <div key={key} className="mb-4">
      <div className="flex items-center gap-2 mb-1">
        <span className="font-mono text-xs text-gray-400">
          [{formatTimestamp(segment.start + offset)}]
        </span>
        <span className={`font-sans font-semibold ${getSpeakerColor(speaker)}`}>{speaker}:</span>
      </div>
      <p className="text-gray-800 pl-4 border-l-2 border-gray-200 text-base leading-[1.7] sm:text-lg">
        {segment.text}
      </p>
    </div>
  )
}

export default function SegmentedTranscriptViewer({ transcript }: SegmentedTranscriptViewerProps) {
  const offset = transcript.playback_time_offset_seconds ?? 0
  const visibleSegments = transcript.segments.filter((seg) => seg.kind !== 'filler')

  if (visibleSegments.length === 0) {
    return (
      <div className="text-center py-12 min-h-[300px]">
        <p className="text-gray-600 font-medium">No content segments available</p>
        <p className="text-sm text-gray-400 mt-1">
          This transcript's segments have all been marked as filler.
        </p>
      </div>
    )
  }

  return (
    <div className="transcript-content leading-relaxed space-y-1">
      {visibleSegments.map((segment) => (
        <Fragment key={segment.id}>
          {segment.kind === 'ad_break'
            ? renderAdBreak(segment, segment.id, offset)
            : renderContent(segment, segment.id, offset)}
        </Fragment>
      ))}
    </div>
  )
}
