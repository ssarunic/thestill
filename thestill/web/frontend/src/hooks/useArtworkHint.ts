import { useLocation } from 'react-router-dom'

// A list that links to a show page hands over the artwork it was already
// showing for that row, so the destination can paint it while its own copy
// loads. Top podcasts needs this: an unimported chart entry shows Apple's
// small chart cover, but the show page renders the RSS original (often a
// multi-megapixel file), which would otherwise leave an empty frame on the
// first open.

export interface ArtworkHintState {
  artworkHint?: string | null
}

/** The artwork the previous page showed for this item, if it passed one. */
export function useArtworkHint(): string | undefined {
  const location = useLocation()
  return (location.state as ArtworkHintState | null)?.artworkHint ?? undefined
}
