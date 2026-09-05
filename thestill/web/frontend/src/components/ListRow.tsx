import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import SmartImage from './SmartImage'

export interface ListRowProps {
  /**
   * Ordinal for ranked lists. A number renders in a two-character column that
   * widens on its own past rank 99; pass a node to swap in a spinner.
   */
  rank?: ReactNode
  /** Artwork or avatar. Use ``ListRowArtwork`` for the standard 48 px tile. */
  leading?: ReactNode
  /** Small line above the title (e.g. podcast name + timestamp on Inbox). */
  overline?: ReactNode
  title: ReactNode
  /** One truncated line under the title. */
  subtitle?: ReactNode
  /** Extra content under the subtitle (progress pill, badges). */
  footer?: ReactNode
  /**
   * Trailing slot. On phones it should hold at most one element — a 44 px
   * action, a badge or a timestamp; text labels return at ``sm``. Rendered
   * above the stretched link so its controls stay clickable.
   */
  trailing?: ReactNode
  /** Row link target; the link is stretched over the whole row. */
  to?: string
  /** ``Link`` state (e.g. spec #52's ``backgroundLocation``). */
  state?: unknown
  /** Row action when there is no link target (e.g. resolve-then-navigate). */
  onClick?: () => void
  /** Dims the row and sets ``aria-busy`` while an async row action runs. */
  busy?: boolean
  /** Accessible name for the row link/button when the title alone is not enough. */
  ariaLabel?: string
  /** ``center`` for single-purpose rows, ``start`` when the body stacks lines. */
  align?: 'center' | 'start'
  /** Weight/colour classes for the title. Defaults to ``font-medium text-gray-900``. */
  titleClassName?: string
  className?: string
}

/**
 * The app's media-object row (spec #73): leading slot, body, trailing slot.
 *
 * The title carries the row's link (or button) and stretches it over the
 * whole row with an ``::after`` overlay, so the row is one real ``<a>`` with
 * no nested interactive content — cmd-click and open-in-new-tab work, and
 * the trailing action sits above the overlay on its own layer.
 */
export default function ListRow({
  rank,
  leading,
  overline,
  title,
  subtitle,
  footer,
  trailing,
  to,
  state,
  onClick,
  busy = false,
  ariaLabel,
  align = 'center',
  titleClassName = 'font-medium text-gray-900',
  className = '',
}: ListRowProps) {
  const titleClasses = `block text-row sm:text-base leading-snug line-clamp-2 ${
    overline ? 'mt-0.5' : ''
  } ${titleClassName}`
  // The overlay is the whole-row hit area; the visible focus ring is drawn on
  // it so keyboard focus outlines the row, not just the title text.
  const stretch =
    'after:absolute after:inset-0 after:content-[""] focus:outline-none focus-visible:after:ring-2 focus-visible:after:ring-inset focus-visible:after:ring-primary-500'

  let titleNode: ReactNode
  if (to) {
    titleNode = (
      <Link to={to} state={state} aria-label={ariaLabel} className={`${titleClasses} ${stretch}`}>
        {title}
      </Link>
    )
  } else if (onClick) {
    titleNode = (
      <button
        type="button"
        onClick={onClick}
        aria-label={ariaLabel}
        className={`${titleClasses} ${stretch} w-full text-left`}
      >
        {title}
      </button>
    )
  } else {
    titleNode = <p className={titleClasses}>{title}</p>
  }

  return (
    <li
      aria-busy={busy || undefined}
      className={`group relative flex gap-3 px-4 py-2.5 sm:py-3 min-h-[64px] transition-colors hover:bg-gray-50 ${
        align === 'center' ? 'items-center' : 'items-start'
      } ${busy ? 'opacity-70' : ''} ${className}`}
    >
      {rank !== undefined && rank !== null && (
        <span className="min-w-[2ch] shrink-0 text-right text-sm font-semibold tabular-nums text-gray-400 flex items-center justify-end">
          {rank}
        </span>
      )}
      {leading}
      <div className="flex-1 min-w-0">
        {overline}
        {titleNode}
        {subtitle !== undefined && subtitle !== null && (
          <div className="mt-0.5 text-xs sm:text-sm text-gray-500 truncate">{subtitle}</div>
        )}
        {footer}
      </div>
      {trailing && (
        <div className="relative z-10 shrink-0 flex items-center gap-2">{trailing}</div>
      )}
    </li>
  )
}

interface ListRowArtworkProps {
  /**
   * Candidate image URLs, most preferred first (e.g. episode artwork, then
   * podcast artwork). Broken URLs advance down the chain before the
   * placeholder shows.
   */
  sources: (string | null | undefined)[]
  /** 48 px for podcast/inbox rows, 40 px for episode rows inside a podcast. */
  size?: 12 | 10
  className?: string
}

/** Standard square thumbnail with the generic-podcast placeholder. */
export function ListRowArtwork({ sources, size = 12, className = '' }: ListRowArtworkProps) {
  const box = size === 12 ? 'w-12 h-12' : 'w-10 h-10'
  return (
    <SmartImage
      sources={sources}
      alt=""
      width={size * 4}
      height={size * 4}
      loading="lazy"
      className={`${box} rounded-md object-cover shrink-0 aspect-square bg-gray-100 ${className}`}
      fallback={
        <div
          aria-hidden="true"
          className={`${box} rounded-md shrink-0 aspect-square bg-gradient-to-br from-primary-100 to-secondary-100 flex items-center justify-center ${className}`}
        >
          <svg className="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
          </svg>
        </div>
      }
    />
  )
}
