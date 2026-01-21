import { ButtonHTMLAttributes, forwardRef } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost'
export type ButtonSize = 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  isLoading?: boolean
  icon?: React.ReactNode
  /** Hide label on mobile, show only icon */
  iconOnlyMobile?: boolean
}

const variantStyles: Record<ButtonVariant, { base: string; disabled: string }> = {
  primary: {
    base: 'bg-indigo-600 text-white hover:bg-indigo-700 active:bg-indigo-800 shadow-sm hover:shadow',
    disabled: 'bg-gray-100 text-gray-400',
  },
  secondary: {
    base: 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50 active:bg-gray-100',
    disabled: 'bg-gray-50 text-gray-400 border-gray-200',
  },
  danger: {
    base: 'text-red-600 hover:bg-red-50 hover:text-red-700 active:bg-red-100',
    disabled: 'bg-gray-100 text-gray-400',
  },
  ghost: {
    base: 'text-gray-600 hover:bg-gray-100 hover:text-gray-900 active:bg-gray-200',
    disabled: 'text-gray-400',
  },
}

const sizeStyles: Record<ButtonSize, string> = {
  sm: 'min-w-[36px] min-h-[36px] px-2.5 sm:px-3 text-xs gap-1.5',
  md: 'min-w-[44px] min-h-[44px] px-3 sm:px-4 text-sm gap-2',
  lg: 'min-w-[48px] min-h-[48px] px-4 sm:px-5 text-base gap-2.5',
}

const iconSizes: Record<ButtonSize, string> = {
  sm: 'w-4 h-4 sm:w-3.5 sm:h-3.5',
  md: 'w-5 h-5 sm:w-4 sm:h-4',
  lg: 'w-6 h-6 sm:w-5 sm:h-5',
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
 * - iconOnlyMobile prop to show only icon on mobile devices
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
    const styles = variantStyles[variant]

    return (
      <button
        ref={ref}
        disabled={isDisabled}
        className={`
          inline-flex items-center justify-center rounded-lg font-medium
          transition-all duration-200
          ${sizeStyles[size]}
          ${isDisabled ? `${styles.disabled} cursor-not-allowed` : styles.base}
          ${className}
        `}
        {...props}
      >
        {isLoading ? (
          <LoadingSpinner size={size} />
        ) : icon ? (
          <span className={iconSizes[size]}>{icon}</span>
        ) : null}
        {children && (
          <span className={iconOnlyMobile ? 'hidden sm:inline' : ''}>
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

export const CloseIcon = () => (
  <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" className="w-full h-full">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
  </svg>
)
