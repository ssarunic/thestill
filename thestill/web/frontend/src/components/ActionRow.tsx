import type { ReactNode } from 'react'

interface ActionRowProps {
  /** The one dominant action: ``<Button size="lg" pill>``. */
  primary: ReactNode
  /** Up to three ``Button size="icon"`` (or equivalent links), in priority order. */
  actions?: ReactNode
  className?: string
}

/**
 * Spec #76 §3.2 / §5.2 — one primary and a few 44 px icon actions, 12 px
 * gaps, never wrapping and never shrinking. Every supported viewport
 * (≥ 320 px) fits the four slots, so the priority-based overflow menu the
 * spec describes is deferred until a fifth slot or a narrower host needs it.
 */
export default function ActionRow({ primary, actions, className = '' }: ActionRowProps) {
  return (
    <div className={`flex flex-nowrap items-center gap-3 ${className}`}>
      {primary}
      {actions}
    </div>
  )
}
