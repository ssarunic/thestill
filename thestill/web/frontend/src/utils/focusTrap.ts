import type { KeyboardEvent as ReactKeyboardEvent } from 'react'

// Elements a focus trap cycles through. Mirrors what a browser considers
// tabbable closely enough for a dialog panel's controls.
export const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Keep Tab / Shift+Tab cycling inside `panel`. Wire it to the panel's
 * `onKeyDown`; it ignores every key but Tab. Focus on the panel itself
 * (its `tabIndex={-1}` landing spot) counts as "before the first control".
 */
export function trapTabKey(e: ReactKeyboardEvent, panel: HTMLElement | null): void {
  if (e.key !== 'Tab' || !panel) return
  const focusable = panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
  if (focusable.length === 0) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (e.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault()
    first.focus()
  }
}
