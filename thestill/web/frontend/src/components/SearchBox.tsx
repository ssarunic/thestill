import type { KeyboardEvent } from 'react'

interface SearchBoxProps {
  value: string
  onChange: (value: string) => void
  placeholder: string
  ariaLabel: string
  inputClassName: string
  testId?: string
}

// Text filter for a list page: magnifier, native search input, an × button
// while there is text, Escape clears. Lifted from the Podcasts page for
// spec #85 (inbox search); Podcasts/TopPodcasts still carry their inline
// copies and migrate in a follow-up. Pair with ``useDebouncedSearchParam``
// so the value lives in the URL and survives Back.
export default function SearchBox({
  value,
  onChange,
  placeholder,
  ariaLabel,
  inputClassName,
  testId,
}: SearchBoxProps) {
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape' && value) {
      e.preventDefault()
      onChange('')
    }
  }
  return (
    <div className="relative">
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        aria-label={ariaLabel}
        className={`rounded-md border border-gray-300 pl-8 pr-7 text-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 ${inputClassName}`}
        data-testid={testId}
      />
      <svg
        className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M11 19a8 8 0 110-16 8 8 0 010 16z" />
      </svg>
      {value && (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label="Clear search"
          className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-sm leading-none px-1"
        >
          ×
        </button>
      )}
    </div>
  )
}
