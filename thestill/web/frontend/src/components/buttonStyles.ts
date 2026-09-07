// Class recipes shared by ``Button`` and the few places that need a button's
// look on another element (an external ``<a>`` in an action row). Kept out
// of ``Button.tsx`` so that file exports only components (react-refresh).

export type ButtonVariant = 'primary' | 'secondary' | 'tonal' | 'success' | 'danger' | 'ghost'
// ``icon``: 44 px circular hit area (spec #76 §5.2). ``iconSm``: the same
// 44 px hit area with a 36 px visual disc — a transparent 4 px border plus
// ``bg-clip-padding`` keeps the touch target at the #73 floor while the
// painted circle stays small enough for a 56 px bar. Use it with the
// ``primary`` or ``ghost`` variants (the ``secondary`` border would paint).
export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon' | 'iconSm'

export const variantStyles: Record<ButtonVariant, { base: string; disabled: string }> = {
  // Navy ``primary`` ramp from tailwind.config.js — the same colour as the
  // sidebar's active item, the logo and the mini player (spec #73 §5.1).
  primary: {
    base: 'bg-primary-900 text-white hover:bg-primary-800 active:bg-primary-700 shadow-sm hover:shadow',
    disabled: 'bg-gray-100 text-gray-400',
  },
  secondary: {
    base: 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50 active:bg-gray-100',
    disabled: 'bg-gray-50 text-gray-400 border-gray-200',
  },
  // Quiet secondary action for list rows (Follow on a chart row).
  tonal: {
    base: 'bg-primary-50 text-primary-900 hover:bg-primary-100 active:bg-primary-200',
    disabled: 'bg-gray-100 text-gray-400',
  },
  // Resting "done" state (Following + check icon). Keeps its tint when disabled because
  // the disabled state *is* the message.
  success: {
    base: 'bg-green-100 text-green-800',
    disabled: 'bg-green-100 text-green-800',
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

export const sizeStyles: Record<ButtonSize, string> = {
  sm: 'min-w-[36px] min-h-[36px] px-2.5 sm:px-3 text-xs gap-1.5 rounded-lg',
  md: 'min-w-[44px] min-h-[44px] px-3 sm:px-4 text-sm gap-2 rounded-lg',
  lg: 'min-w-[48px] min-h-[48px] px-4 sm:px-5 text-base gap-2.5 rounded-lg',
  icon: 'w-11 h-11 p-0 rounded-full shrink-0',
  iconSm: 'w-11 h-11 p-0 rounded-full shrink-0 border-4 border-transparent bg-clip-padding',
}

export const iconSizes: Record<ButtonSize, string> = {
  sm: 'w-4 h-4 sm:w-3.5 sm:h-3.5',
  md: 'w-5 h-5 sm:w-4 sm:h-4',
  lg: 'w-6 h-6 sm:w-5 sm:h-5',
  icon: 'w-5 h-5',
  iconSm: 'w-5 h-5',
}

export interface ButtonClassOptions {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Pill radius for the text sizes (the primary action of a detail page). */
  pill?: boolean
  disabled?: boolean
  className?: string
}

export function buttonClassName({
  variant = 'primary',
  size = 'md',
  pill = false,
  disabled = false,
  className = '',
}: ButtonClassOptions): string {
  const styles = variantStyles[variant]
  // ``rounded-full`` must come after the size's ``rounded-lg`` in the
  // stylesheet to win; both are emitted by Tailwind in source order, so the
  // pill override is applied here instead of leaking into ``className``.
  const radius = pill ? 'rounded-full' : ''
  return [
    'inline-flex items-center justify-center font-medium transition-all duration-200',
    sizeStyles[size],
    radius,
    disabled ? `${styles.disabled} cursor-not-allowed` : styles.base,
    className,
  ]
    .filter(Boolean)
    .join(' ')
}
