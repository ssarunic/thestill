import { useCallback } from 'react'
import { useToast } from '../components/Toast'
import { track } from '../analytics/client'

/**
 * Share a link: the native Web Share sheet where the browser has one, a
 * clipboard copy (with a toast) elsewhere. Used by the episode page's More
 * menu (`EpisodeMoreMenu`, spec #90).
 */
export function useShareAction(title: string, url: string, kind: 'episode' = 'episode') {
  const { showToast } = useToast()
  const canShare = typeof navigator !== 'undefined' && !!navigator.share

  const share = useCallback(async () => {
    if (canShare) {
      try {
        await navigator.share({ title, url })
        track('share', { kind }) // spec #95 usage event: the kind, never the URL
        // No success toast for native share - OS handles feedback
      } catch (err) {
        // Only show an error when it wasn't the user cancelling
        if (err instanceof Error && err.name !== 'AbortError') showToast('Failed to share', 'error')
      }
      return
    }
    try {
      await navigator.clipboard.writeText(url)
      track('share', { kind })
      showToast('Link copied to clipboard', 'success')
    } catch {
      showToast('Failed to copy link', 'error')
    }
  }, [canShare, title, url, showToast, kind])

  return {
    share,
    canShare,
    label: canShare ? 'Share' : 'Copy link',
    hint: canShare ? 'Share episode' : 'Copy link to clipboard',
  }
}
