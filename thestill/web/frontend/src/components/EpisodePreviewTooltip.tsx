import { useEpisodeSummary } from '../hooks/useApi'

interface EpisodePreviewTooltipProps {
  podcastSlug: string
  episodeSlug: string
}

export default function EpisodePreviewTooltip({ podcastSlug, episodeSlug }: EpisodePreviewTooltipProps) {
  const { data, isLoading, error } = useEpisodeSummary(podcastSlug, episodeSlug)

  // Extract preview content from summary
  const getPreviewContent = (): { summary: string; quotes: string[] } | null => {
    if (!data?.content || !data.available) return null

    const content = data.content
    let summaryPreview = ''
    const quotes: string[] = []

    // Try to extract executive summary section
    const summaryMatch = content.match(/##\s*(?:Executive\s*)?Summary\s*\n+([\s\S]*?)(?=\n##|\n---|\Z)/i)
    if (summaryMatch) {
      summaryPreview = summaryMatch[1].trim().split('\n').slice(0, 3).join(' ').substring(0, 200)
      if (summaryPreview.length === 200) summaryPreview += '...'
    } else {
      // Fallback: take first 3 lines of content
      summaryPreview = content.split('\n').filter(line => line.trim() && !line.startsWith('#')).slice(0, 3).join(' ').substring(0, 200)
      if (summaryPreview.length === 200) summaryPreview += '...'
    }

    // Try to extract notable quotes
    const quotesMatch = content.match(/##\s*(?:Notable\s*)?Quotes?\s*\n+([\s\S]*?)(?=\n##|\n---|\Z)/i)
    if (quotesMatch) {
      const quotesSection = quotesMatch[1]
      // Extract quoted text (lines starting with > or containing quotes)
      const quoteLines = quotesSection.match(/(?:^>?\s*"[^"]+"|^>\s*.+$)/gm)
      if (quoteLines) {
        quotes.push(...quoteLines.slice(0, 2).map(q => q.replace(/^>\s*/, '').trim()))
      }
    }

    return { summary: summaryPreview, quotes }
  }

  const preview = getPreviewContent()

  return (
    <div className="absolute z-50 top-full left-0 right-0 mt-2 p-4 bg-white rounded-lg shadow-lg border border-gray-200 max-w-lg">
      {isLoading && (
        <div className="flex items-center gap-2 text-gray-500 text-sm">
          <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-indigo-600"></div>
          Loading preview...
        </div>
      )}

      {error && (
        <p className="text-sm text-red-500">Failed to load preview</p>
      )}

      {!isLoading && !error && !preview && (
        <p className="text-sm text-gray-500 italic">No summary available</p>
      )}

      {preview && (
        <div className="space-y-3">
          {/* Summary preview */}
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Summary</h4>
            <p className="text-sm text-gray-700">{preview.summary}</p>
          </div>

          {/* Notable quotes */}
          {preview.quotes.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Notable Quotes</h4>
              <ul className="space-y-1">
                {preview.quotes.map((quote, i) => (
                  <li key={i} className="text-sm text-gray-600 italic">
                    {quote.length > 80 ? quote.substring(0, 80) + '...' : quote}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Click to view link */}
          <p className="text-xs text-indigo-600">Click to view full details</p>
        </div>
      )}
    </div>
  )
}
