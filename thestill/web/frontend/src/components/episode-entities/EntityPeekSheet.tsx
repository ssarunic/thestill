import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useBodyScrollLock } from '../../hooks/useBodyScrollLock'
import { useSwipeDownToClose } from '../../hooks/useSwipeDownToClose'
import { trapTabKey } from '../../utils/focusTrap'

// Phone shell for the entity peek. Shares its bones with the Now Playing
// sheet (spec #72): scrim, bottom-anchored panel on the transient rung
// (`z-[70]`, spec #71), body scroll lock, focus trap, Esc / scrim tap /
// swipe-down close. Nothing here navigates, so dismissing it leaves the
// reader exactly where it was.

export interface EntityPeekSheetProps {
  label: string
  onClose: () => void
  children: ReactNode
}

// The sheet is portaled to <body>, but React events still bubble up the
// component tree into the seekable transcript segment that opened it.
function stopReactPropagation(e: MouseEvent | ReactKeyboardEvent) {
  e.stopPropagation()
}

export default function EntityPeekSheet({ label, onClose, children }: EntityPeekSheetProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const { dragY, handleProps } = useSwipeDownToClose(onClose)

  useBodyScrollLock(true)

  // Take focus on open and hand it back to the opener on close so a
  // keyboard/screen-reader user is not stranded at the top of the page.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    return () => opener?.focus?.()
  }, [])

  // Capture phase so this runs before the reader overlay's bubbling
  // `document` listener and can claim the key.
  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      e.preventDefault()
      onClose()
    }
    document.addEventListener('keydown', onKey, { capture: true })
    return () => document.removeEventListener('keydown', onKey, { capture: true })
  }, [onClose])

  if (typeof document === 'undefined') return null

  return createPortal(
    <div className="fixed inset-0 z-[70]" data-testid="entity-peek-sheet-root" onClick={stopReactPropagation}>
      <div className="absolute inset-0 bg-black/50" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        data-testid="entity-peek-sheet"
        onKeyDown={(e) => {
          trapTabKey(e, panelRef.current)
          stopReactPropagation(e)
        }}
        style={dragY ? { transform: `translateY(${dragY}px)` } : undefined}
        className="absolute inset-x-0 bottom-0 flex max-h-[80vh] flex-col overflow-y-auto overscroll-contain rounded-t-2xl bg-white shadow-xl outline-none pb-[env(safe-area-inset-bottom)]"
      >
        <div
          className="flex cursor-grab touch-none justify-center py-2 active:cursor-grabbing"
          {...handleProps}
          data-testid="entity-peek-drag-handle"
          aria-hidden="true"
        >
          <span className="h-1.5 w-10 rounded-full bg-gray-300" />
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}
