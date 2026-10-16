interface SegmentedControlProps<T extends string> {
  options: readonly { key: T; label: string }[]
  value: T
  onChange: (value: T) => void
  /** Names the group for assistive tech, e.g. "Inbox view". */
  ariaLabel: string
}

/**
 * A small set of mutually exclusive buttons (inbox views, analytics ranges).
 * Each button reports its state with ``aria-pressed``.
 */
export default function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: SegmentedControlProps<T>) {
  return (
    <div role="group" aria-label={ariaLabel} className="inline-flex rounded-lg bg-gray-100 p-0.5">
      {options.map(({ key, label }) => (
        <button
          key={key}
          type="button"
          aria-pressed={value === key}
          onClick={() => onChange(key)}
          className={`px-3 py-1 text-sm font-medium rounded-md transition-colors ${
            value === key ? 'bg-surface text-ink shadow-sm' : 'text-gray-600 hover:text-ink'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  )
}
