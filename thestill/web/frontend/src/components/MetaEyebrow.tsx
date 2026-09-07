import { Fragment, type ReactNode } from 'react'

interface MetaEyebrowProps {
  /** Falsy entries are dropped, so callers can pass conditionals inline. */
  items: (ReactNode | null | undefined | false)[]
  className?: string
}

/**
 * Spec #76 §5.3 — the one-line metadata eyebrow above a detail-page title
 * (``SUN 6 SEP · S3 E12 · EXPLICIT``). Items are joined by a middle dot that
 * screen readers hear as a comma. Renders nothing when every item is empty.
 */
export default function MetaEyebrow({ items, className = '' }: MetaEyebrowProps) {
  const visible = items.filter((item) => item !== null && item !== undefined && item !== false && item !== '')
  if (visible.length === 0) return null
  return (
    <p className={`text-eyebrow uppercase tracking-wide text-gray-500 ${className}`}>
      {visible.map((item, index) => (
        <Fragment key={index}>
          {index > 0 && (
            <>
              <span aria-hidden="true"> · </span>
              <span className="sr-only">, </span>
            </>
          )}
          <span>{item}</span>
        </Fragment>
      ))}
    </p>
  )
}
