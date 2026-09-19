import type { BriefingPodcastGroup } from '../api/types'
import Artwork from './Artwork'
import { ARTWORK_ROLE } from './artworkRoles'
import SmartImage from './SmartImage'

/** How many shows the mosaic can hold: a 2 × 2 grid. */
const COVER_MAX_SHOWS = 4

interface BriefingCoverProps {
  /** Podcast groups in briefing order; the first four supply the tiles. */
  podcasts: BriefingPodcastGroup[]
  className?: string
}

/**
 * The briefing hero's artwork: the covered podcasts' artwork as one tile.
 * One show fills the tile; two split it side by side; three give the
 * first show the left half; four or more fill a 2 × 2 grid. The whole
 * tile is one image to assistive tech, named after the shows it holds.
 */
export default function BriefingCover({ podcasts, className = '' }: BriefingCoverProps) {
  const shows = podcasts.slice(0, COVER_MAX_SHOWS)
  if (shows.length === 0) return null

  const { box, radius } = ARTWORK_ROLE.collage
  if (shows.length === 1) {
    return (
      <Artwork
        role="collage"
        sources={[shows[0].image_url]}
        alt={`${shows[0].title} artwork`}
        loading="eager"
        className={className}
      />
    )
  }

  const label = `Artwork from ${shows.map((show) => show.title).join(', ')}`
  const spanClass = (index: number) =>
    shows.length === 2 || (shows.length === 3 && index === 0) ? 'row-span-2' : ''

  return (
    <div
      role="img"
      aria-label={label}
      className={`${box} ${radius} grid shrink-0 grid-cols-2 grid-rows-2 gap-px overflow-hidden bg-hairline shadow-sm ${className}`}
    >
      {shows.map((show, index) => (
        <SmartImage
          key={show.id}
          sources={[show.image_url]}
          alt=""
          loading="eager"
          width={64}
          height={64}
          className={`h-full w-full min-h-0 object-cover ${spanClass(index)}`}
          fallback={
            <div
              aria-hidden="true"
              className={`h-full w-full min-h-0 bg-gradient-to-br from-primary-100 to-secondary-100 ${spanClass(index)}`}
            />
          }
        />
      ))}
    </div>
  )
}
