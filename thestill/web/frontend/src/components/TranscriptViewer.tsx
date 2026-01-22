import { useMemo, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { TranscriptType } from '../api/types'
import { useAudioPlayerOptional } from '../contexts/AudioPlayerContext'

interface TranscriptViewerProps {
  content: string
  isLoading?: boolean
  available?: boolean
  episodeState?: string
  transcriptType?: TranscriptType
}

// Convert timestamp string to seconds
function parseTimestampToSeconds(timestamp: string): number {
  const parts = timestamp.split(':').map(Number)
  if (parts.length === 2) {
    // MM:SS format
    return parts[0] * 60 + parts[1]
  } else if (parts.length === 3) {
    // HH:MM:SS format
    return parts[0] * 3600 + parts[1] * 60 + parts[2]
  }
  return 0
}

interface ParsedSegment {
  type: 'speaker' | 'markdown'
  content: string
  speaker?: string
  timestamp?: string
  timestampSeconds?: number
}

// Parse transcript to separate speaker segments from regular markdown
function parseTranscript(content: string): ParsedSegment[] {
  const lines = content.split('\n')
  const segments: ParsedSegment[] = []

  let markdownBuffer: string[] = []

  const flushMarkdown = () => {
    if (markdownBuffer.length > 0) {
      segments.push({
        type: 'markdown',
        content: markdownBuffer.join('\n'),
      })
      markdownBuffer = []
    }
  }

  for (const line of lines) {
    // Check for speaker line: [00:00] [SPEAKER_01] or [00:00:00] [SPEAKER_01]
    const speakerMatch = line.match(/^\[(\d{2}:\d{2}(?::\d{2})?)\]\s*\[([^\]]+)\]\s*(.*)/)
    if (speakerMatch) {
      flushMarkdown()
      const timestamp = speakerMatch[1]
      segments.push({
        type: 'speaker',
        timestamp,
        timestampSeconds: parseTimestampToSeconds(timestamp),
        speaker: speakerMatch[2],
        content: speakerMatch[3],
      })
      continue
    }

    // Check for timestamped bold speaker: [00:00] **Name:** text
    const timestampedBoldMatch = line.match(/^\[(\d{2}:\d{2}(?::\d{2})?)\]\s*\*\*([^*]+)\*\*:\s*(.*)/)
    if (timestampedBoldMatch) {
      flushMarkdown()
      const timestamp = timestampedBoldMatch[1]
      segments.push({
        type: 'speaker',
        timestamp,
        timestampSeconds: parseTimestampToSeconds(timestamp),
        speaker: timestampedBoldMatch[2],
        content: timestampedBoldMatch[3],
      })
      continue
    }

    // Check for bold speaker without timestamp: **Name:**
    const boldSpeakerMatch = line.match(/^\*\*([^*]+)\*\*:\s*(.*)/)
    if (boldSpeakerMatch) {
      flushMarkdown()
      segments.push({
        type: 'speaker',
        speaker: boldSpeakerMatch[1],
        content: boldSpeakerMatch[2],
      })
      continue
    }

    // Accumulate as markdown
    markdownBuffer.push(line)
  }

  flushMarkdown()
  return segments
}

// Map speaker IDs to colors
const speakerColors: Record<string, string> = {
  'SPEAKER_00': 'text-blue-700',
  'SPEAKER_01': 'text-purple-700',
  'SPEAKER_02': 'text-green-700',
  'SPEAKER_03': 'text-orange-700',
  'SPEAKER_04': 'text-pink-700',
}

function getSpeakerColor(speaker: string): string {
  if (speakerColors[speaker]) return speakerColors[speaker]
  // Generate consistent color for named speakers
  const hash = speaker.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0)
  const colors = ['text-blue-700', 'text-purple-700', 'text-green-700', 'text-orange-700', 'text-pink-700', 'text-indigo-700', 'text-red-700']
  return colors[hash % colors.length]
}

// Get status message based on episode state
function getTranscriptStatus(state?: string): { title: string; description: string; icon: 'pending' | 'progress' } {
  switch (state) {
    case 'discovered':
      return {
        title: 'Transcription pending',
        description: 'This episode is queued for download and transcription.',
        icon: 'pending',
      }
    case 'downloaded':
      return {
        title: 'Transcription pending',
        description: 'Audio downloaded. Waiting to be transcribed.',
        icon: 'pending',
      }
    case 'downsampled':
      return {
        title: 'Transcription in progress',
        description: 'Audio is being transcribed. This may take a few minutes.',
        icon: 'progress',
      }
    case 'transcribed':
      return {
        title: 'Cleaning in progress',
        description: 'Transcript is being cleaned and formatted.',
        icon: 'progress',
      }
    default:
      return {
        title: 'Transcript not yet available',
        description: 'This episode hasn\'t been processed yet.',
        icon: 'pending',
      }
  }
}

// Find the index of the currently playing segment based on playback time
function findCurrentSegmentIndex(segments: ParsedSegment[], currentTime: number): number {
  let currentIndex = -1
  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i]
    if (segment.type === 'speaker' && segment.timestampSeconds !== undefined) {
      if (segment.timestampSeconds <= currentTime) {
        currentIndex = i
      } else {
        break
      }
    }
  }
  return currentIndex
}

interface TimestampButtonProps {
  timestamp: string
  timestampSeconds: number
  onSeek: (seconds: number) => void
  isActive: boolean
}

function TimestampButton({ timestamp, timestampSeconds, onSeek, isActive }: TimestampButtonProps) {
  return (
    <button
      onClick={() => onSeek(timestampSeconds)}
      className={`font-mono text-xs px-1.5 py-0.5 rounded transition-colors ${
        isActive
          ? 'bg-primary-100 text-primary-700 font-medium'
          : 'text-gray-400 hover:bg-gray-100 hover:text-gray-600'
      }`}
      title={`Jump to ${timestamp}`}
    >
      [{timestamp}]
    </button>
  )
}

export default function TranscriptViewer({ content, isLoading, available, episodeState, transcriptType }: TranscriptViewerProps) {
  const segments = useMemo(() => parseTranscript(content), [content])
  const playerContext = useAudioPlayerOptional()
  const currentTime = playerContext?.currentTime ?? 0
  const isPlaying = playerContext?.isPlaying ?? false
  const seekTo = playerContext?.seekTo

  // Find current segment for highlighting
  const currentSegmentIndex = useMemo(
    () => (isPlaying ? findCurrentSegmentIndex(segments, currentTime) : -1),
    [segments, currentTime, isPlaying]
  )

  // Auto-scroll to current segment when playing
  const segmentRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const lastScrolledIndex = useRef<number>(-1)

  useEffect(() => {
    if (isPlaying && currentSegmentIndex >= 0 && currentSegmentIndex !== lastScrolledIndex.current) {
      const element = segmentRefs.current.get(currentSegmentIndex)
      if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' })
        lastScrolledIndex.current = currentSegmentIndex
      }
    }
  }, [currentSegmentIndex, isPlaying])

  // Reset scroll tracking when playback stops
  useEffect(() => {
    if (!isPlaying) {
      lastScrolledIndex.current = -1
    }
  }, [isPlaying])

  const handleSeek = (seconds: number) => {
    seekTo?.(seconds)
  }

  if (isLoading) {
    return (
      <div className="space-y-4 min-h-[300px]">
        {[...Array(10)].map((_, i) => (
          <div key={i} className="animate-pulse">
            <div className="h-4 bg-gray-200 rounded w-1/4 mb-2" />
            <div className="h-4 bg-gray-200 rounded w-full mb-1" />
            <div className="h-4 bg-gray-200 rounded w-3/4" />
          </div>
        ))}
      </div>
    )
  }

  if (!available) {
    const status = getTranscriptStatus(episodeState)
    return (
      <div className="text-center py-12 min-h-[300px]">
        {status.icon === 'progress' ? (
          <div className="w-16 h-16 mx-auto mb-4 flex items-center justify-center">
            <div className="animate-spin rounded-full h-12 w-12 border-4 border-primary-200 border-t-primary-600"></div>
          </div>
        ) : (
          <svg className="w-16 h-16 mx-auto text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        )}
        <p className="text-gray-600 font-medium">{status.title}</p>
        <p className="text-sm text-gray-400 mt-1">{status.description}</p>
      </div>
    )
  }

  return (
    <div className="transcript-content font-serif leading-relaxed space-y-4">
      {/* Raw transcript notice */}
      {transcriptType === 'raw' && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-6">
          <div className="flex items-start gap-3">
            <svg className="w-5 h-5 text-amber-500 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <div>
              <p className="text-amber-800 font-medium text-sm">Raw Transcript</p>
              <p className="text-amber-700 text-sm mt-1">
                This is the raw transcript from speech-to-text. It hasn't been cleaned yet, so it may contain
                transcription errors, speaker labeling issues, or formatting inconsistencies.
              </p>
            </div>
          </div>
        </div>
      )}
      {segments.map((segment, index) => {
        if (segment.type === 'speaker') {
          const isCurrentSegment = index === currentSegmentIndex
          return (
            <div
              key={index}
              ref={(el) => {
                if (el) segmentRefs.current.set(index, el)
                else segmentRefs.current.delete(index)
              }}
              className={`mb-4 transition-colors duration-300 rounded-lg -mx-2 px-2 py-1 ${
                isCurrentSegment ? 'bg-primary-50 ring-1 ring-primary-200' : ''
              }`}
            >
              <div className="flex items-center gap-2 mb-1">
                {segment.timestamp && segment.timestampSeconds !== undefined && seekTo ? (
                  <TimestampButton
                    timestamp={segment.timestamp}
                    timestampSeconds={segment.timestampSeconds}
                    onSeek={handleSeek}
                    isActive={isCurrentSegment}
                  />
                ) : segment.timestamp ? (
                  <span className="font-mono text-xs text-gray-400">[{segment.timestamp}]</span>
                ) : null}
                <span className={`font-sans font-semibold ${getSpeakerColor(segment.speaker || '')}`}>
                  {segment.speaker}:
                </span>
              </div>
              <p className="text-gray-800 pl-4 border-l-2 border-gray-200 text-base leading-[1.7] sm:text-lg">
                {segment.content}
              </p>
            </div>
          )
        }

        // Render markdown sections with prose styling
        return (
          <div key={index} className="prose prose-gray max-w-none prose-headings:font-sans prose-h1:text-xl prose-h1:font-semibold prose-h2:text-lg prose-h2:font-semibold prose-h3:text-base prose-h3:font-semibold prose-p:text-gray-700 prose-p:my-2">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {segment.content}
            </ReactMarkdown>
          </div>
        )
      })}
    </div>
  )
}
