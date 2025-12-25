import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface TranscriptViewerProps {
  content: string
  isLoading?: boolean
  available?: boolean
  episodeState?: string
}

// Parse transcript to separate speaker segments from regular markdown
function parseTranscript(content: string) {
  const lines = content.split('\n')
  const segments: Array<{
    type: 'speaker' | 'markdown'
    content: string
    speaker?: string
    timestamp?: string
  }> = []

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
      segments.push({
        type: 'speaker',
        timestamp: speakerMatch[1],
        speaker: speakerMatch[2],
        content: speakerMatch[3],
      })
      continue
    }

    // Check for bold speaker: **Name:**
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

export default function TranscriptViewer({ content, isLoading, available, episodeState }: TranscriptViewerProps) {
  const segments = useMemo(() => parseTranscript(content), [content])

  if (isLoading) {
    return (
      <div className="space-y-4">
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
      <div className="text-center py-12">
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
      {segments.map((segment, index) => {
        if (segment.type === 'speaker') {
          return (
            <div key={index} className="mb-4">
              <div className="flex items-center gap-2 mb-1">
                {segment.timestamp && (
                  <span className="font-mono text-xs text-gray-400">[{segment.timestamp}]</span>
                )}
                <span className={`font-sans font-semibold ${getSpeakerColor(segment.speaker || '')}`}>
                  {segment.speaker}:
                </span>
              </div>
              <p className="text-gray-800 pl-4 border-l-2 border-gray-200">
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
