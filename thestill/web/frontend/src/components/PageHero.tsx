import type { ReactNode, Ref } from 'react'
import SmartImage from './SmartImage'

interface PageHeroProps {
  /** An ``<Artwork role="hero" | "card">`` element, or omitted for pages without art. */
  artwork?: ReactNode
  /** Sources for the blurred copy behind the hero; decorative, hidden under reduced transparency. */
  backdropSources?: (string | null | undefined)[]
  /** A ``<MetaEyebrow>``. */
  eyebrow?: ReactNode
  title: ReactNode
  /** Lets the host observe the title for the collapsing header (spec #76 §3.7). */
  titleRef?: Ref<HTMLHeadingElement>
  /** The identity row under the title: show row, byline. */
  identity?: ReactNode
  /** Action row, description — anything that belongs in the title column. */
  children?: ReactNode
  className?: string
}

/**
 * Spec #76 §3.1 / §5 — the detail-page hero: artwork centred on phones and
 * left of the text block from ``sm``, then eyebrow, title and identity row,
 * all left-aligned at every width. Layout only; the page decides what goes
 * in each slot, which is what lets Episode, Podcast and Briefing detail
 * share it without a discriminator prop.
 */
export default function PageHero({
  artwork,
  backdropSources,
  eyebrow,
  title,
  titleRef,
  identity,
  children,
  className = '',
}: PageHeroProps) {
  return (
    <header className={`relative ${className}`}>
      {backdropSources && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -inset-x-4 -top-4 bottom-0 -z-10 overflow-hidden transparency-reduce:hidden sm:-inset-x-6 lg:-inset-x-8"
        >
          <SmartImage
            sources={backdropSources}
            alt=""
            className="h-full w-full scale-125 object-cover opacity-20 blur-2xl"
            fallback={null}
          />
          <div className="absolute inset-0 bg-gradient-to-b from-transparent to-gray-50" />
        </div>
      )}
      <div className="flex flex-col gap-4 sm:flex-row sm:gap-6">
        {artwork && <div className="flex justify-center sm:block">{artwork}</div>}
        <div className="min-w-0 flex-1 space-y-2">
          {eyebrow}
          <h1 ref={titleRef} className="text-title sm:text-title-lg text-gray-900">
            {title}
          </h1>
          {identity}
          {children && <div className="space-y-4 pt-2">{children}</div>}
        </div>
      </div>
    </header>
  )
}
