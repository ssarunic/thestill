import SmartImage from './SmartImage'
import { ARTWORK_ROLE, type ArtworkRole } from './artworkRoles'

export type { ArtworkRole } from './artworkRoles'

interface ArtworkProps {
  /** Candidate URLs, most preferred first; broken ones advance down the chain. */
  sources: (string | null | undefined)[]
  role: ArtworkRole
  /** Empty for decorative artwork next to its own title. */
  alt?: string
  loading?: 'eager' | 'lazy'
  className?: string
}

export default function Artwork({ sources, role, alt = '', loading = 'lazy', className = '' }: ArtworkProps) {
  const { box, radius, px, glyph } = ARTWORK_ROLE[role]
  return (
    <SmartImage
      sources={sources}
      alt={alt}
      width={px}
      height={px}
      loading={loading}
      className={`${box} ${radius} object-cover shrink-0 aspect-square bg-gray-100 ${className}`}
      fallback={
        <div
          aria-hidden={alt ? undefined : 'true'}
          role={alt ? 'img' : undefined}
          aria-label={alt || undefined}
          className={`${box} ${radius} shrink-0 aspect-square bg-gradient-to-br from-primary-100 to-secondary-100 flex items-center justify-center ${className}`}
        >
          <svg className={`${glyph} text-primary-400`} fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
          </svg>
        </div>
      }
    />
  )
}
