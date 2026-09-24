import { useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

// Swipe-down on a phone sheet's drag handle closes it past this travel.
export const SWIPE_CLOSE_PX = 80

export interface SwipeHandleProps {
  onPointerDown: (e: ReactPointerEvent<HTMLDivElement>) => void
  onPointerMove: (e: ReactPointerEvent<HTMLDivElement>) => void
  onPointerUp: (e: ReactPointerEvent<HTMLDivElement>) => void
  onPointerCancel: (e: ReactPointerEvent<HTMLDivElement>) => void
}

/**
 * The swipe-down-to-close gesture shared by every bottom sheet (Now
 * Playing, the entity peek). Spread `handleProps` onto the drag handle;
 * translate the panel by `dragY` while the finger is down.
 *
 * A cancelled pointer (the browser took the gesture over, e.g. for a
 * scroll) resets the sheet without closing it: its `clientY` is stale.
 */
export function useSwipeDownToClose(onClose: () => void): { dragY: number; handleProps: SwipeHandleProps } {
  const [dragY, setDragY] = useState(0)
  const dragRef = useRef<{ pointerId: number; startY: number } | null>(null)

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    dragRef.current = { pointerId: e.pointerId, startY: e.clientY }
    e.currentTarget.setPointerCapture?.(e.pointerId)
  }
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== e.pointerId) return
    setDragY(Math.max(0, e.clientY - drag.startY))
  }
  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== e.pointerId) return
    dragRef.current = null
    const travelled = e.clientY - drag.startY
    setDragY(0)
    if (travelled >= SWIPE_CLOSE_PX) onClose()
  }
  const onPointerCancel = (e: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== e.pointerId) return
    dragRef.current = null
    setDragY(0)
  }

  return { dragY, handleProps: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel } }
}
