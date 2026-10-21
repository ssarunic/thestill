import { useState } from 'react'
import SmartImage from './SmartImage'
import { ARTWORK_ROLE, LANDSCAPE_MIN_RATIO, type ArtworkRole } from './artworkRoles'

interface ArtworkProps {
  /** Candidate URLs, most preferred first; broken ones advance down the chain. */
  sources: (string | null | undefined)[]
  role: ArtworkRole
  /** Empty for decorative artwork next to its own title. */
  alt?: string
  loading?: 'eager' | 'lazy'
  className?: string
  /**
   * An image the browser already has (e.g. the list thumbnail the user just
   * clicked). Painted behind the frame until the first source loads, and the
   * last fallback if every source fails.
   */
  placeholder?: string | null
}

export default function Artwork({ sources, role, alt = '', loading = 'lazy', className = '', placeholder }: ArtworkProps) {
  const { box, radius, px, glyph, landscapeBox } = ARTWORK_ROLE[role]
  // Only known once the image has loaded; roles without a landscape frame stay square.
  const [landscape, setLandscape] = useState(false)
  const frame = landscape && landscapeBox ? landscapeBox : `${box} aspect-square`
  const candidates = placeholder ? [...sources, placeholder] : sources
  // Keyed on the candidate set so a new item shows its placeholder again.
  const key = candidates.join('|')
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  // Dropped after load so transparent artwork doesn't show the placeholder through it.
  const showPlaceholder = !!placeholder && loadedKey !== key
  return (
    <SmartImage
      sources={candidates}
      alt={alt}
      width={px}
      height={px}
      loading={loading}
      onLoad={(e) => {
        setLoadedKey(key)
        if (landscapeBox) {
          const { naturalWidth, naturalHeight } = e.currentTarget
          setLandscape(naturalHeight > 0 && naturalWidth / naturalHeight >= LANDSCAPE_MIN_RATIO)
        }
      }}
      style={
        showPlaceholder
          ? { backgroundImage: `url(${JSON.stringify(placeholder)})`, backgroundSize: 'cover', backgroundPosition: 'center' }
          : undefined
      }
      className={`${frame} ${radius} object-cover shrink-0 bg-gray-100 ${className}`}
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
