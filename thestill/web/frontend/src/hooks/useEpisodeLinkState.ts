import { useLocation, type Location } from 'react-router-dom'
import { useBackgroundLocation } from './useBackgroundLocation'

export interface EpisodeLinkState {
  /** Router `state` for a `<Link>` into the episode, or undefined for a plain push. */
  state: { backgroundLocation: Location } | undefined
  /** The current route already is this episode — a link should be a no-op, not a duplicate push. */
  alreadyHere: boolean
}

/**
 * Spec #52 overlay contract for links into an episode from player surfaces
 * (spec #71 bar, spec #72 sheet): from `/inbox`, or from inside an overlay
 * already open over it, the link carries `backgroundLocation` so the reader
 * opens above the still-mounted list. Elsewhere it is a plain navigation to
 * the standalone page. One hook so the bar's "Show video" link, the sheet's
 * title link and "Open transcript here" cannot drift apart.
 */
export function useEpisodeLinkState(episodePath: string): EpisodeLinkState {
  const location = useLocation()
  const backgroundLocation = useBackgroundLocation()
  const inInbox = location.pathname === '/inbox' || location.pathname.startsWith('/inbox/')
  const state = backgroundLocation
    ? { backgroundLocation }
    : inInbox
      ? { backgroundLocation: location }
      : undefined
  return { state, alreadyHere: location.pathname === episodePath }
}
