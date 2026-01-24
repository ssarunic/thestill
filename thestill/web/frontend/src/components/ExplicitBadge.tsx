/**
 * ExplicitBadge - Displays an "E" badge for explicit content.
 *
 * Follows the Apple Podcasts style: a small, muted circle with "E" letter.
 * Only renders when explicit is true, returns null otherwise.
 */

interface ExplicitBadgeProps {
  explicit?: boolean | null
  className?: string
}

export function ExplicitBadge({ explicit, className = '' }: ExplicitBadgeProps) {
  if (!explicit) return null

  return (
    <span
      className={`inline-flex items-center justify-center w-4 h-4 rounded bg-gray-200 text-gray-600 text-[10px] font-bold flex-shrink-0 ${className}`}
      title="Explicit content"
    >
      E
    </span>
  )
}
