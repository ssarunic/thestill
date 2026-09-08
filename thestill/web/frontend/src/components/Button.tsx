import { ButtonHTMLAttributes, forwardRef } from 'react'
import { buttonClassName, iconSizes, type ButtonSize, type ButtonVariant } from './buttonStyles'

export type { ButtonSize, ButtonVariant } from './buttonStyles'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Pill radius — the primary action of a detail page (spec #76 §3.2). */
  pill?: boolean
  isLoading?: boolean
  icon?: React.ReactNode
  /** Hide label on mobile, show only icon */
  iconOnlyMobile?: boolean
}

const LoadingSpinner = ({ size }: { size: ButtonSize }) => (
  <svg className={`${iconSizes[size]} animate-spin`} fill="none" viewBox="0 0 24 24">
    <circle
      className="opacity-25"
      cx="12"
      cy="12"
      r="10"
      stroke="currentColor"
      strokeWidth="4"
    />
    <path
      className="opacity-75"
      fill="currentColor"
      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
    />
  </svg>
)

/**
 * Reusable Button component with consistent styling across the app.
 *
 * Features:
 * - Mobile-first with touch-friendly sizes (min 44px tap target)
 * - Responsive icon/text sizing
 * - Loading state with spinner
 * - iconOnlyMobile prop to show only the icon on mobile devices; the label
 *   stays in the DOM as screen-reader-only text so the button keeps its name
 * - ``icon`` / ``iconSm`` sizes for circular icon-only actions (spec #76
 *   §5.2): pass the glyph as ``icon`` and a ``sr-only`` label as children
 *
 * @example
 * // Primary button with icon
 * <Button icon={<PlusIcon />}>Follow</Button>
 *
 * // Icon-only on mobile
 * <Button icon={<RefreshIcon />} iconOnlyMobile>Refresh Feeds</Button>
 *
 * // Danger variant
 * <Button variant="danger" icon={<TrashIcon />}>Unfollow</Button>
 */
const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = 'primary',
      size = 'md',
      pill = false,
      isLoading = false,
      icon,
      iconOnlyMobile = false,
      disabled,
      children,
      className = '',
      ...props
    },
    ref
  ) => {
    const isDisabled = disabled || isLoading

    return (
      <button
        ref={ref}
        disabled={isDisabled}
        className={buttonClassName({ variant, size, pill, disabled: isDisabled, className })}
        {...props}
      >
        {isLoading ? (
          <LoadingSpinner size={size} />
        ) : icon ? (
          <span className={iconSizes[size]}>{icon}</span>
        ) : null}
        {children && (
          <span className={iconOnlyMobile ? 'sr-only sm:not-sr-only' : ''}>
            {children}
          </span>
        )}
      </button>
    )
  }
)

Button.displayName = 'Button'

export default Button

// Common icons as separate exports for convenience
export const PlusIcon = () => (
  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
  </svg>
)

export const RefreshIcon = () => (
  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full">
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
    />
  </svg>
)

export const MinusIcon = () => (
  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
  </svg>
)

export const CheckIcon = () => (
  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
  </svg>
)

export const CloseIcon = () => (
  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
  </svg>
)

export const ExternalLinkIcon = () => (
  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full" aria-hidden="true">
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
    />
  </svg>
)

export const PlayIcon = () => (
  <svg fill="currentColor" viewBox="0 0 24 24" className="w-full h-full" aria-hidden="true">
    <path d="M8 5v14l11-7z" />
  </svg>
)

export const PauseIcon = () => (
  <svg fill="currentColor" viewBox="0 0 24 24" className="w-full h-full" aria-hidden="true">
    <rect x="6" y="5" width="4" height="14" rx="1" />
    <rect x="14" y="5" width="4" height="14" rx="1" />
  </svg>
)

export const YouTubeIcon = () => (
  <svg fill="currentColor" viewBox="0 0 24 24" className="w-full h-full" aria-hidden="true">
    <path d="M21.6 7.2a2.5 2.5 0 00-1.76-1.77C18.25 5 12 5 12 5s-6.25 0-7.84.43A2.5 2.5 0 002.4 7.2 26.2 26.2 0 002 12c0 1.62.13 3.23.4 4.8a2.5 2.5 0 001.76 1.77C5.75 19 12 19 12 19s6.25 0 7.84-.43a2.5 2.5 0 001.76-1.77c.27-1.57.4-3.18.4-4.8 0-1.62-.13-3.23-.4-4.8zM10 15.5v-7l6 3.5-6 3.5z" />
  </svg>
)

export const ShareIcon = () => (
  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full" aria-hidden="true">
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z"
    />
  </svg>
)

export const LinkIcon = () => (
  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full" aria-hidden="true">
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"
    />
  </svg>
)

export const ChevronUpIcon = () => (
  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full" aria-hidden="true">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
  </svg>
)

export const ChevronRightIcon = () => (
  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full" aria-hidden="true">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
  </svg>
)
