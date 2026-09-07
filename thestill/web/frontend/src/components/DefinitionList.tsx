import type { ReactNode } from 'react'

export interface DefinitionRow {
  label: string
  /** ``null`` / ``undefined`` / ``''`` rows are omitted. */
  value: ReactNode | null | undefined
  /** Right-aligned ``tabular-nums`` for times and counts. */
  numeric?: boolean
}

interface DefinitionListProps {
  heading?: string
  rows: DefinitionRow[]
  className?: string
}

/**
 * Spec #76 §5.4 — labelled facts as a ``<dl>``: label left, value right,
 * hairline dividers. Used by the episode Information section, the podcast
 * facts block and Settings. Callers pass every row unconditionally; empty
 * values drop out here so the omission rule lives in one place.
 */
export default function DefinitionList({ heading, rows, className = '' }: DefinitionListProps) {
  const visible = rows.filter((row) => row.value !== null && row.value !== undefined && row.value !== '')
  if (visible.length === 0) return null
  return (
    <section className={className} aria-label={heading}>
      {heading && <h2 className="text-section text-ink mb-1">{heading}</h2>}
      <dl className="divide-y divide-gray-100">
        {visible.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-4 py-2.5">
            <dt className="shrink-0 text-sm text-muted">{row.label}</dt>
            <dd className={`min-w-0 text-right text-sm text-ink ${row.numeric ? 'tabular-nums' : ''}`}>
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
