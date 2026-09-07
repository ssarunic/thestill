import type { HTMLAttributes, ReactNode } from 'react'

interface PanelProps extends HTMLAttributes<HTMLElement> {
  as?: 'div' | 'section'
  children: ReactNode
}

/**
 * Spec #76 §5.1 ``panel`` surface tier: below ``sm`` a plain white band
 * that runs full-bleed (undoing the layout's ``p-4``) with no side border or
 * radius; from ``sm`` a bordered, rounded card. Generalises ``ListGroup``'s
 * rule from lists to detail pages — on phones nothing draws a frame inside
 * the screen's frame.
 */
export default function Panel({ as: Tag = 'div', className = '', children, ...rest }: PanelProps) {
  return (
    <Tag
      className={`bg-surface border-y border-hairline -mx-4 sm:mx-0 sm:border sm:rounded-lg ${className}`}
      {...rest}
    >
      {children}
    </Tag>
  )
}
