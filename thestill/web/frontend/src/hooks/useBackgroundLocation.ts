import { useLocation, type Location } from 'react-router-dom'

// Spec #52 — inbox reader overlay. When the inbox opens an episode it pushes
// the canonical episode URL with the inbox location stashed in navigation
// state. The presence of that state is what selects overlay rendering; the
// URL itself never changes shape.

export interface BackgroundLocationState {
  backgroundLocation?: Location
}

/** The location the current overlay was opened over, if any. */
export function useBackgroundLocation(): Location | undefined {
  const location = useLocation()
  return (location.state as BackgroundLocationState | null)?.backgroundLocation
}

/**
 * Nav-highlight derivation (spec #52 §"Sidebar highlight"): while an overlay
 * is open the address bar shows the episode URL, but the page the user is
 * *in* is the background one — so active-state matching uses the background
 * location when present. Single helper shared by the desktop sidebar and the
 * mobile drawer so the two surfaces cannot drift.
 *
 * Matching mirrors NavLink's default (non-`end`) semantics: the item is
 * active on the exact path or any sub-path.
 */
export function useIsNavActive(to: string): boolean {
  const location = useLocation()
  const effective =
    (location.state as BackgroundLocationState | null)?.backgroundLocation ?? location
  return effective.pathname === to || effective.pathname.startsWith(`${to}/`)
}
