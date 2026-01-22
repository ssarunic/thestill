import { type ReactNode, type ComponentPropsWithoutRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useAudioPlayerOptional } from '../contexts/AudioPlayerContext'

interface SummaryViewerProps {
  content: string
  isLoading?: boolean
  available?: boolean
  episodeState?: string
}

// Get status message based on episode state
function getSummaryStatus(state?: string): { title: string; description: string; icon: 'pending' | 'progress' } {
  switch (state) {
    case 'discovered':
      return {
        title: 'Summarization pending',
        description: 'This episode is queued for processing.',
        icon: 'pending',
      }
    case 'downloaded':
      return {
        title: 'Summarization pending',
        description: 'Audio downloaded. Waiting to be transcribed first.',
        icon: 'pending',
      }
    case 'downsampled':
      return {
        title: 'Summarization pending',
        description: 'Audio is being transcribed. Summarization will follow.',
        icon: 'pending',
      }
    case 'transcribed':
      return {
        title: 'Summarization pending',
        description: 'Transcript is being cleaned. Summarization will follow.',
        icon: 'pending',
      }
    case 'cleaned':
      return {
        title: 'Summarization in progress',
        description: 'Episode is being summarized. This may take a minute.',
        icon: 'progress',
      }
    default:
      return {
        title: 'Summary not yet available',
        description: 'This episode hasn\'t been summarized yet.',
        icon: 'pending',
      }
  }
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

// Regex to find timestamps in text: [00:00] or [00:00:00]
const timestampRegex = /\[(\d{2}:\d{2}(?::\d{2})?)\]/g

// Parse text and replace timestamps with clickable buttons
function parseTextWithTimestamps(
  text: string,
  onSeek: ((seconds: number) => void) | undefined
): ReactNode[] {
  if (!onSeek) {
    return [text]
  }

  const parts: ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  // Reset regex state
  timestampRegex.lastIndex = 0

  while ((match = timestampRegex.exec(text)) !== null) {
    // Add text before the timestamp
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index))
    }

    // Add clickable timestamp button
    const timestamp = match[1]
    const seconds = parseTimestampToSeconds(timestamp)
    parts.push(
      <button
        key={`${match.index}-${timestamp}`}
        onClick={() => onSeek(seconds)}
        className="font-mono text-xs px-1.5 py-0.5 rounded text-primary-600 hover:bg-primary-50 hover:text-primary-700 transition-colors"
        title={`Jump to ${timestamp}`}
      >
        [{timestamp}]
      </button>
    )

    lastIndex = match.index + match[0].length
  }

  // Add remaining text after last timestamp
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }

  return parts.length > 0 ? parts : [text]
}

export default function SummaryViewer({ content, isLoading, available, episodeState }: SummaryViewerProps) {
  const playerContext = useAudioPlayerOptional()
  const seekTo = playerContext?.seekTo

  if (isLoading) {
    return (
      <div className="space-y-6 min-h-[300px]">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="animate-pulse">
            <div className="h-5 bg-gray-200 rounded w-1/3 mb-3" />
            <div className="space-y-2">
              <div className="h-4 bg-gray-200 rounded w-full" />
              <div className="h-4 bg-gray-200 rounded w-5/6" />
              <div className="h-4 bg-gray-200 rounded w-4/6" />
            </div>
          </div>
        ))}
      </div>
    )
  }

  if (!available) {
    const status = getSummaryStatus(episodeState)
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

  // Custom components to render text with clickable timestamps
  const components = {
    // Handle paragraph text
    p: ({ children, ...props }: ComponentPropsWithoutRef<'p'>) => {
      const processedChildren = processChildren(children, seekTo)
      return <p {...props}>{processedChildren}</p>
    },
    // Handle list items
    li: ({ children, ...props }: ComponentPropsWithoutRef<'li'>) => {
      const processedChildren = processChildren(children, seekTo)
      return <li {...props}>{processedChildren}</li>
    },
    // Handle blockquotes
    blockquote: ({ children, ...props }: ComponentPropsWithoutRef<'blockquote'>) => {
      const processedChildren = processChildren(children, seekTo)
      return <blockquote {...props}>{processedChildren}</blockquote>
    },
    // Handle table cells
    td: ({ children, ...props }: ComponentPropsWithoutRef<'td'>) => {
      const processedChildren = processChildren(children, seekTo)
      return <td {...props}>{processedChildren}</td>
    },
  }

  return (
    <div className="prose prose-gray max-w-none prose-base sm:prose-lg prose-headings:text-primary-900 prose-h1:text-xl prose-h1:sm:text-2xl prose-h2:text-lg prose-h2:sm:text-xl prose-h2:mt-6 prose-h2:mb-3 prose-h3:text-base prose-h3:sm:text-lg prose-h3:mt-5 prose-h3:mb-2 prose-h4:text-base prose-h4:mt-4 prose-h4:mb-2 prose-blockquote:border-l-secondary-400 prose-blockquote:bg-secondary-50 prose-blockquote:py-3 prose-blockquote:px-4 prose-blockquote:not-italic prose-blockquote:text-gray-700 prose-li:marker:text-gray-400">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}

// Helper to process children and replace timestamp strings with buttons
function processChildren(
  children: ReactNode,
  onSeek: ((seconds: number) => void) | undefined
): ReactNode {
  if (!onSeek) return children
  if (!children) return children

  if (typeof children === 'string') {
    const parsed = parseTextWithTimestamps(children, onSeek)
    return parsed.length === 1 ? parsed[0] : <>{parsed}</>
  }

  if (Array.isArray(children)) {
    return children.map((child, index) => {
      if (typeof child === 'string') {
        const parsed = parseTextWithTimestamps(child, onSeek)
        return parsed.length === 1 ? (
          <span key={index}>{parsed[0]}</span>
        ) : (
          <span key={index}>{parsed}</span>
        )
      }
      return child
    })
  }

  return children
}
