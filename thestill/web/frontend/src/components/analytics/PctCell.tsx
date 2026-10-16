import { formatPct, isSmallGroup, SMALL_GROUP } from '../../utils/analyticsFormat'

interface PctCellProps {
  numerator: number
  denominator: number
  pct: number | null
}

/**
 * A percentage with the count it rests on beside it (spec #95 D1). A
 * denominator under SMALL_GROUP is greyed out, never hidden; zero renders a
 * dash so "not measurable yet" never reads as 0%.
 */
export default function PctCell({ numerator, denominator, pct }: PctCellProps) {
  if (denominator === 0) {
    return <span className="text-muted">—</span>
  }
  const small = isSmallGroup(denominator)
  return (
    <span
      className={`whitespace-nowrap tabular-nums ${small ? 'opacity-60' : ''}`}
      title={small ? `Fewer than ${SMALL_GROUP} users: read with care` : undefined}
      data-small={small || undefined}
    >
      <span className="text-ink font-medium">{formatPct(pct)}</span>{' '}
      <span className="text-muted text-xs">
        ({numerator}/{denominator})
      </span>
    </span>
  )
}
