import SmartImage from './SmartImage'

// Spec #76 §5.8 — one wrapper fixes size and radius per role, replacing the
// seven size/radius combinations spec #73 §2 counted.
export type ArtworkRole = 'inline' | 'rowSm' | 'row' | 'card' | 'hero'

const ROLE: Record<ArtworkRole, { box: string; radius: string; px: number; glyph: string }> = {
  /** 28 px — the show row under a title, the collapsed bar. */
  inline: { box: 'w-7 h-7', radius: 'rounded-md', px: 28, glyph: 'w-3.5 h-3.5' },
  /** 40 px — episode rows inside a podcast. */
  rowSm: { box: 'w-10 h-10', radius: 'rounded-lg', px: 40, glyph: 'w-5 h-5' },
  /** 48 px — podcast and inbox rows (``ListRowArtwork``). */
  row: { box: 'w-12 h-12', radius: 'rounded-lg', px: 48, glyph: 'w-5 h-5' },
  /** 96 px — podcast detail. */
  card: { box: 'w-24 h-24', radius: 'rounded-lg', px: 96, glyph: 'w-10 h-10' },
  /**
   * Episode hero: 40 vw capped at 160 px on phones (the largest size that
   * keeps the tabs above the fold at 393 × 852, spec #76 §7.1), 200 px
   * from ``sm``.
   */
  hero: { box: 'w-[40vw] max-w-[160px] sm:w-[200px] sm:max-w-none', radius: 'rounded-xl', px: 200, glyph: 'w-12 h-12' },
}

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
  const { box, radius, px, glyph } = ROLE[role]
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
