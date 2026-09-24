import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

// Phone shell for the entity peek. Mirrors the Now Playing sheet
// (spec #72): scrim, bottom-anchored panel on the transient rung
// (`z-[70]`, spec #71), Esc / scrim tap / swipe-down close. Nothing here
// navigates, so dismissing it leaves the reader exactly where it was.

const SWIPE_CLOSE_PX = 80

export interface EntityPeekSheetProps {
  label: string
  onClose: () => void
  children: ReactNode
}

export default function EntityPeekSheet({ label, onClose, children }: EntityPeekSheetProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const [dragY, setDragY] = useState(0)
  const dragRef = useRef<{ pointerId: number; startY: number } | null>(null)

  // Take focus on open and hand it back to the opener on close so a
  // keyboard/screen-reader user is not stranded at the top of the page.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    return () => opener?.focus?.()
  }, [])

  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      // Claim the key so the reader overlay underneath doesn't close too.
      e.preventDefault()
      onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const onHandlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    dragRef.current = { pointerId: e.pointerId, startY: e.clientY }
    e.currentTarget.setPointerCapture?.(e.pointerId)
  }
  const onHandlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== e.pointerId) return
    setDragY(Math.max(0, e.clientY - drag.startY))
  }
  const onHandlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== e.pointerId) return
    dragRef.current = null
    const travelled = e.clientY - drag.startY
    setDragY(0)
    if (travelled >= SWIPE_CLOSE_PX) onClose()
  }

  if (typeof document === 'undefined') return null

  return createPortal(
    <div className="fixed inset-0 z-[70]" data-testid="entity-peek-sheet-root">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        data-testid="entity-peek-sheet"
        style={dragY ? { transform: `translateY(${dragY}px)` } : undefined}
        className="absolute inset-x-0 bottom-0 flex max-h-[80vh] flex-col overflow-y-auto rounded-t-2xl bg-white shadow-xl outline-none pb-[env(safe-area-inset-bottom)]"
      >
        <div
          className="flex cursor-grab touch-none justify-center py-2 active:cursor-grabbing"
          onPointerDown={onHandlePointerDown}
          onPointerMove={onHandlePointerMove}
          onPointerUp={onHandlePointerUp}
          onPointerCancel={onHandlePointerUp}
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
