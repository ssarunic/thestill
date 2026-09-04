import type { HTMLAttributes, ReactNode } from 'react'

interface ListGroupProps extends HTMLAttributes<HTMLElement> {
  /** ``ol`` for ranked lists (Top podcasts), ``ul`` otherwise. */
  as?: 'ul' | 'ol'
  children: ReactNode
}

/**
 * Container for a run of ``ListRow``s (spec #73).
 *
 * One white surface with hairline dividers replaces the card-per-row pattern.
 * Below ``sm`` the group runs full-bleed: it pulls itself out to the screen
 * edge (undoing the layout's ``p-4``), drops the side borders and radius, and
 * relies on the rows' own ``px-4`` so their content edge lines up with the
 * page heading. From ``sm`` up it is a bordered, rounded card.
 */
export default function ListGroup({
  as: Tag = 'ul',
  className = '',
  children,
  ...rest
}: ListGroupProps) {
  return (
    <Tag
      className={`bg-white divide-y divide-gray-100 border-y border-gray-200 -mx-4 sm:mx-0 sm:border sm:rounded-lg sm:overflow-hidden ${className}`}
      {...rest}
    >
      {children}
    </Tag>
  )
}
