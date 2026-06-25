import { useState } from 'react'
import { expandImageCandidates } from '../utils/imageFallback'

type SmartImageProps = Omit<
  React.ImgHTMLAttributes<HTMLImageElement>,
  'src'
> & {
  /** Ordered list of candidate image URLs (most preferred first). */
  sources: (string | null | undefined)[]
  /** Rendered when no source is provided or every candidate fails to load. */
  fallback: React.ReactNode
}

/**
 * An <img> that lazily walks a fallback chain. On load error it advances to the
 * next candidate (Transistor signed URLs self-heal to their origin via
 * expandImageCandidates); once all candidates are exhausted it renders the
 * provided fallback placeholder instead of a broken-image glyph.
 */
export default function SmartImage({
  sources,
  fallback,
  ...imgProps
}: SmartImageProps) {
  const candidates = expandImageCandidates(sources)
  const key = candidates.join('|')
  const [index, setIndex] = useState(0)

  // Reset during render when the candidate set changes (component reused for a
  // new item) — the React-recommended alternative to a state-resetting effect.
  const [prevKey, setPrevKey] = useState(key)
  if (key !== prevKey) {
    setPrevKey(key)
    setIndex(0)
  }

  const src = candidates[index]
  if (!src) return <>{fallback}</>

  return (
    <img {...imgProps} src={src} onError={() => setIndex((i) => i + 1)} />
  )
}
