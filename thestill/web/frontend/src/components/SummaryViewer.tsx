import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

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

export default function SummaryViewer({ content, isLoading, available, episodeState }: SummaryViewerProps) {
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

  return (
    <div className="prose prose-gray max-w-none prose-base sm:prose-lg prose-headings:text-primary-900 prose-h1:text-xl prose-h1:sm:text-2xl prose-h2:text-lg prose-h2:sm:text-xl prose-h2:mt-6 prose-h2:mb-3 prose-h3:text-base prose-h3:sm:text-lg prose-h3:mt-5 prose-h3:mb-2 prose-h4:text-base prose-h4:mt-4 prose-h4:mb-2 prose-blockquote:border-l-secondary-400 prose-blockquote:bg-secondary-50 prose-blockquote:py-3 prose-blockquote:px-4 prose-blockquote:not-italic prose-blockquote:text-gray-700 prose-li:marker:text-gray-400">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
