import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import type { AnnotatedSegment, EpisodeEntity } from '../../api/types'
import { buildPeople } from './buildPeople'

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  const first = parts[0]?.[0] ?? ''
  const last = parts.length > 1 ? parts[parts.length - 1][0] : ''
  return `${first}${last}`.toUpperCase()
}

interface PeopleProps {
  entities: EpisodeEntity[]
  /** Segmented transcript rows; ``null`` for legacy transcripts (no speaker labels). */
  segments: AnnotatedSegment[] | null | undefined
  /** A plain speaker chip was tapped: jump the transcript to that speaker. */
  onSpeakerSelect: (speakerLabel: string) => void
}

export default function People({ entities, segments, onSpeakerSelect }: PeopleProps) {
  const chips = useMemo(() => buildPeople(entities, segments), [entities, segments])
  if (chips.length === 0) return null

  return (
    <section aria-label="People">
      <h2 className="text-section text-gray-900 mb-3">People</h2>
      <ul className="flex gap-4 overflow-x-auto pb-1">
        {chips.map((chip) => {
          const avatar = chip.imageUrl ? (
            <img src={chip.imageUrl} alt="" className="h-14 w-14 rounded-full object-cover" />
          ) : (
            <span
              aria-hidden="true"
              className="flex h-14 w-14 items-center justify-center rounded-full text-base font-semibold text-white"
              style={{ backgroundColor: chip.color }}
            >
              {initials(chip.name)}
            </span>
          )
          const body = (
            <>
              {avatar}
              <span className="line-clamp-2 w-16 text-center text-xs leading-tight text-gray-700">{chip.name}</span>
            </>
          )
          const chipClass =
            'flex flex-col items-center gap-1.5 rounded-lg p-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400'
          return (
            <li key={chip.key} className="shrink-0">
              {chip.href ? (
                <Link to={chip.href} className={chipClass}>
                  {body}
                </Link>
              ) : (
                <button
                  type="button"
                  onClick={() => onSpeakerSelect(chip.name)}
                  title={`Find ${chip.name} in the transcript`}
                  className={chipClass}
                >
                  {body}
                </button>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
