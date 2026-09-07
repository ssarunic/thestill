import { useCallback, useState, useEffect } from 'react'
import { useToast } from './Toast'
import Button, { LinkIcon, ShareIcon } from './Button'

interface ShareButtonProps {
  title: string
  url: string
  className?: string
  /** Spec #76 §3.2 — a 44 px circular icon action with a screen-reader label. */
  iconOnly?: boolean
}

/**
 * Share button that uses native Web Share API on supported browsers,
 * falling back to copy-to-clipboard on desktop/unsupported browsers.
 */
export default function ShareButton({ title, url, className = '', iconOnly = false }: ShareButtonProps) {
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

  const label = canShare ? 'Share' : 'Copy link'
  const hint = canShare ? 'Share episode' : 'Copy link to clipboard'

  if (iconOnly) {
    return (
      <Button
        variant="secondary"
        size="icon"
        icon={canShare ? <ShareIcon /> : <LinkIcon />}
        onClick={handleShare}
        title={hint}
        className={className}
      >
        <span className="sr-only">{label}</span>
      </Button>
    )
  }

  return (
    <button
      onClick={handleShare}
      className={`flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 transition-colors ${className}`}
      title={hint}
    >
      <span className="h-4 w-4">{canShare ? <ShareIcon /> : <LinkIcon />}</span>
      <span className="hidden sm:inline">{label}</span>
    </button>
  )
}
