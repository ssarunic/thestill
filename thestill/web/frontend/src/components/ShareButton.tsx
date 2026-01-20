import { useCallback, useState, useEffect } from 'react'
import { useToast } from './Toast'

interface ShareButtonProps {
  title: string
  url: string
  className?: string
}

/**
 * Share button that uses native Web Share API on supported browsers,
 * falling back to copy-to-clipboard on desktop/unsupported browsers.
 */
export default function ShareButton({ title, url, className = '' }: ShareButtonProps) {
  const { showToast } = useToast()
  const [canShare, setCanShare] = useState(false)

  // Check if Web Share API is available
  useEffect(() => {
    setCanShare(typeof navigator !== 'undefined' && !!navigator.share)
  }, [])

  const handleShare = useCallback(async () => {
    if (canShare) {
      try {
        await navigator.share({
          title,
          url,
        })
        // Note: No success toast for native share - OS handles feedback
      } catch (err) {
        // User cancelled share or error occurred
        if (err instanceof Error && err.name !== 'AbortError') {
          // Only show error if it wasn't user cancellation
          showToast('Failed to share', 'error')
        }
      }
    } else {
      // Fallback: copy to clipboard
      try {
        await navigator.clipboard.writeText(url)
        showToast('Link copied to clipboard', 'success')
      } catch {
        showToast('Failed to copy link', 'error')
      }
    }
  }, [canShare, title, url, showToast])

  return (
    <button
      onClick={handleShare}
      className={`flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 transition-colors ${className}`}
      title={canShare ? 'Share episode' : 'Copy link to clipboard'}
    >
      {canShare ? (
        // Share icon for native share
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z"
          />
        </svg>
      ) : (
        // Link icon for copy-to-clipboard fallback
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"
          />
        </svg>
      )}
      <span className="hidden sm:inline">{canShare ? 'Share' : 'Copy link'}</span>
    </button>
  )
}
