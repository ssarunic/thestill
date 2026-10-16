import { useShareAction } from '../hooks/useShareAction'
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
  const { share: handleShare, canShare, label, hint } = useShareAction(title, url)

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
      className={`flex items-center gap-1.5 text-sm text-muted hover:text-gray-700 transition-colors ${className}`}
      title={hint}
    >
      <span className="h-4 w-4">{canShare ? <ShareIcon /> : <LinkIcon />}</span>
      <span className="hidden sm:inline">{label}</span>
    </button>
  )
}
