interface SummaryViewerProps {
  content: string
  isLoading?: boolean
  available?: boolean
  episodeState?: string
}

// Simple markdown renderer for summary content
function renderMarkdown(content: string) {
  const lines = content.split('\n')
  const elements: JSX.Element[] = []
  let currentList: string[] = []
  let inBlockquote = false
  let blockquoteContent: string[] = []

  const flushList = () => {
    if (currentList.length > 0) {
      elements.push(
        <ul key={`list-${elements.length}`} className="list-disc list-inside space-y-1 mb-4 text-gray-700">
          {currentList.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )
      currentList = []
    }
  }

  const flushBlockquote = () => {
    if (blockquoteContent.length > 0) {
      elements.push(
        <blockquote key={`quote-${elements.length}`} className="border-l-4 border-secondary-400 bg-secondary-50 py-3 px-4 my-4 italic text-gray-700">
          {blockquoteContent.join(' ')}
        </blockquote>
      )
      blockquoteContent = []
      inBlockquote = false
    }
  }

  for (const line of lines) {
    // Heading
    if (line.startsWith('# ')) {
      flushList()
      flushBlockquote()
      elements.push(
        <h2 key={`h1-${elements.length}`} className="text-xl font-bold text-primary-900 mt-6 mb-3">
          {line.slice(2)}
        </h2>
      )
      continue
    }
    if (line.startsWith('## ')) {
      flushList()
      flushBlockquote()
      elements.push(
        <h3 key={`h2-${elements.length}`} className="text-lg font-semibold text-primary-800 mt-5 mb-2">
          {line.slice(3)}
        </h3>
      )
      continue
    }
    if (line.startsWith('### ')) {
      flushList()
      flushBlockquote()
      elements.push(
        <h4 key={`h3-${elements.length}`} className="text-base font-semibold text-primary-700 mt-4 mb-2">
          {line.slice(4)}
        </h4>
      )
      continue
    }

    // Blockquote
    if (line.startsWith('> ')) {
      flushList()
      inBlockquote = true
      blockquoteContent.push(line.slice(2))
      continue
    } else if (inBlockquote && line.trim() === '') {
      flushBlockquote()
      continue
    } else if (inBlockquote) {
      blockquoteContent.push(line)
      continue
    }

    // List item
    if (line.match(/^[-*]\s+/)) {
      flushBlockquote()
      currentList.push(line.replace(/^[-*]\s+/, ''))
      continue
    }

    // Regular paragraph
    if (line.trim()) {
      flushList()
      flushBlockquote()

      // Handle bold and italic
      let text = line
      text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      text = text.replace(/\*([^*]+)\*/g, '<em>$1</em>')

      elements.push(
        <p
          key={`p-${elements.length}`}
          className="text-gray-700 mb-3"
          dangerouslySetInnerHTML={{ __html: text }}
        />
      )
    }
  }

  flushList()
  flushBlockquote()

  return elements
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

export default function SummaryViewer({ content, isLoading, available, episodeState }: SummaryViewerProps) {
  if (isLoading) {
    return (
      <div className="space-y-6">
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
    <div className="summary-content">
      {renderMarkdown(content)}
    </div>
  )
}
