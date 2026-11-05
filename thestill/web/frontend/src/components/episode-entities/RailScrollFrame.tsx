import { useRef, type ReactNode } from 'react'
import { abovePlayer } from '../../constants/layers'
import { useCanScrollDown } from '../../hooks/useCanScrollDown'

// Vertical room the frame gives up besides the mini player: the pin offset
// plus a matching gap at the bottom. Both reader hosts come to the same
// total — the page pins at 4.5rem under the 3.5rem collapsed episode bar
// (+1rem below), the overlay pins at 1rem inside a scroll area that already
// sits 3.5rem below the viewport top (+1rem below).
const RESERVED_HEIGHT = '5.5rem'

export interface RailScrollFrameProps {
  // `page`: the window scrolls and the collapsed episode bar (h-14, sticky
  // top-0) pins over the top of the page. `overlay`: the overlay panel
  // scrolls below its own header, and the collapsed bar lives in that header.
  host: 'page' | 'overlay'
  children: ReactNode
}

/**
 * The episode reader's right-rail container. Pinned while the transcript
 * scrolls, and capped at the visible height so a rail taller than the
 * screen scrolls on its own: its bottom is reachable without scrolling the
 * whole transcript past it. A fade at the bottom edge says there is more
 * when the scrollbar is hidden (macOS overlay scrollbars).
 */
export default function RailScrollFrame({ host, children }: RailScrollFrameProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const canScrollDown = useCanScrollDown(scrollRef)

  return (
    <div className={`sticky ${host === 'page' ? 'top-[4.5rem]' : 'top-4'}`}>
      <div
        ref={scrollRef}
        data-testid="entity-rail-scroll"
        className="overflow-y-auto overscroll-contain rounded-lg border border-hairline bg-surface p-4"
        style={{ maxHeight: `calc(100dvh - ${abovePlayer()} - ${RESERVED_HEIGHT})` }}
      >
        {children}
      </div>
      {canScrollDown && (
        <div
          data-testid="entity-rail-more-below"
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-px bottom-px h-12 rounded-b-lg bg-gradient-to-t from-surface to-transparent"
        />
      )}
    </div>
  )
}
